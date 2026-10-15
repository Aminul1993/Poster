/** BufferService: Buffer scheduling through the Poster server. */
import { API_CONFIG, RULES, SERVER, STORAGE_KEYS } from '../config.js';
import { ApiError } from '../errors.js';
import { prepareUploadImage } from '../image.js';
import { isPlainObject, truncate } from '../util.js';

/** Display names and colours for Buffer services. */
export const SERVICE_META = Object.freeze({
  instagram: ['Instagram', '#d6246e'], facebook: ['Facebook', '#1877f2'], linkedin: ['LinkedIn', '#0a66c2'],
  twitter: ['X', '#111827'], threads: ['Threads', '#1f2937'], bluesky: ['Bluesky', '#1185fe'], mastodon: ['Mastodon', '#6364ff'],
  pinterest: ['Pinterest', '#e60023'], tiktok: ['TikTok', '#111111'], youtube: ['YouTube', '#ff0000'],
  googlebusiness: ['Google Business', '#4285f4'], startpage: ['Start Page', '#2c4bff'], substack: ['Substack', '#ff6719']
});

/** @param {string} service @returns {{label: string, color: string}} */
export function serviceMeta(service) {
  const key = String(service || '').toLowerCase().replace(/[^a-z]/g, '');
  const meta = SERVICE_META[key];
  return meta ? { label: meta[0], color: meta[1] } : { label: service ? service.charAt(0).toUpperCase() + service.slice(1) : 'Channel', color: '#64748b' };
}

/** Post statuses after which a post can no longer change: published, or being published now. */
export const PUBLISHED_STATUSES = RULES.publishedStatuses;

/** Statuses each filter of the posts list shows (null: all), as the server applies them. */
export const POST_FILTERS = RULES.postFilters;

/**
 * A post as the posts list shows it, read from Buffer.
 * @typedef {{postId: string, status: string, published: boolean, dueAt: string|null, sentAt: string|null, createdAt: string|null,
 *            text: string, channelId: string, channelName: string, service: string, thumbnail: string|null,
 *            externalLink: string|null, error: string|null}} BufferPost
 */

/** @param {object} p @returns {BufferPost} */
export function normalizePost(p) {
  const text = (value) => (typeof value === 'string' && value ? value : null);
  const https = (value) => (typeof value === 'string' && value.startsWith('https://') ? value : null);
  return {
    postId: String(p.postId), status: String(p.status || ''), published: PUBLISHED_STATUSES.includes(p.status),
    dueAt: text(p.dueAt), sentAt: text(p.sentAt), createdAt: text(p.createdAt), text: String(p.text ?? ''),
    channelId: String(p.channelId || ''), channelName: String(p.channelName || ''), service: String(p.service || ''),
    // Demo posts keep the browser-made thumbnail as a data: URL.
    thumbnail: https(p.thumbnail) || (String(p.thumbnail || '').startsWith('data:image/') ? p.thumbnail : null),
    externalLink: https(p.externalLink), error: text(p.error)
  };
}

/**
 * Sorts posts the way the server does for a filter: upcoming soonest first,
 * everything else newest first.
 * @param {BufferPost[]} posts @param {string} filter @returns {BufferPost[]}
 */
export function sortPosts(posts, filter) {
  const key = (p) => p.dueAt || p.createdAt || '';
  const direction = filter === 'upcoming' ? 1 : -1;
  return [...posts].sort((a, b) => direction * key(a).localeCompare(key(b)));
}

/**
 * Checks the server's answer to listing, editing or deleting posts.
 * @param {object} data
 * @returns {{posts: BufferPost[], next: Object<string, string>|null, deleted: string[],
 *            failures: Array<{postId: string, message: string, reason: 'published'|'missing'|'error'}>}}
 */
export function normalizePostResults(data) {
  const posts = Array.isArray(data.posts) ? data.posts.filter((p) => isPlainObject(p) && p.postId) : [];
  const failures = Array.isArray(data.failures) ? data.failures.filter((f) => isPlainObject(f) && f.postId) : [];
  return {
    posts: posts.map(normalizePost),
    next: isPlainObject(data.next) && Object.keys(data.next).length ? data.next : null,
    deleted: Array.isArray(data.deleted) ? data.deleted.map(String) : [],
    failures: failures.map((f) => ({
      postId: String(f.postId), message: String(f.message || 'Unknown error'),
      reason: ['published', 'missing'].includes(f.reason) ? f.reason : 'error'
    }))
  };
}

/**
 * Buffer through the Poster server. The server holds the personal access
 * token and talks to Buffer's GraphQL API; the browser caches the account and
 * channels (keyed by a fingerprint of the server's token) so the channel
 * picker works without reconnecting.
 */
export class BufferService {
  /**
   * @param {{api: ApiService, validation: ValidationService, notifier: NotificationService,
   *          storage: StorageService, getSettings: () => object}} deps
   */
  constructor({ api, validation, notifier, storage, getSettings }) {
    this.api = api;
    this.validation = validation;
    this.notifier = notifier;
    this.storage = storage;
    this.getSettings = getSettings;
  }

  /** @returns {boolean} */
  get isDemo() { return false; }

  /* ----------------------------------------------------- connection cache */

  /**
   * Cached account and channels from the last successful connect. Ignored
   * when the server's token has changed since then.
   * @returns {{account: object, channels: object[], connectedAt: string, tokenId: string}|null}
   */
  getConnection() {
    const all = this.storage.get(STORAGE_KEYS.connection, null);
    const live = isPlainObject(all) && isPlainObject(all.live) ? all.live : null;
    if (!live || !Array.isArray(live.channels)) return null;
    if (!SERVER.buffer.tokenId || live.tokenId !== SERVER.buffer.tokenId) return null;
    return live;
  }

  /** @param {{account: object, channels: object[], tokenId?: string}} connection */
  saveConnection(connection) {
    this.storage.set(STORAGE_KEYS.connection, {
      live: {
        account: connection.account,
        channels: connection.channels,
        connectedAt: new Date().toISOString(),
        tokenId: connection.tokenId || SERVER.buffer.tokenId
      }
    });
  }

  /** @returns {object[]} known channels */
  channels() {
    return this.getConnection()?.channels || [];
  }

  /** @returns {string[]} channel ids selected for this mode */
  selectedChannelIds(settings = this.getSettings()) {
    return [...(settings.channelIds?.live || [])];
  }

  /** @param {string} id @returns {string} */
  channelName(id) {
    const channel = this.channels().find((c) => c.id === id);
    return channel ? `${channel.name} (${serviceMeta(channel.service).label})` : `Channel ${truncate(id, 12)}`;
  }

  /**
   * Detects selected channels that are unknown, disconnected or locked.
   * @param {string[]} ids @returns {string|null} problem description
   */
  checkChannels(ids) {
    const known = this.channels();
    if (!known.length) return null;
    for (const id of ids) {
      const channel = known.find((c) => c.id === id);
      if (!channel) return `A selected channel (${truncate(id, 14)}) no longer exists in your Buffer account. Reconnect in Settings → Buffer.`;
      if (!channel.usable) return `${channel.name} (${serviceMeta(channel.service).label}) is ${channel.isDisconnected ? 'disconnected' : 'locked'} in Buffer. Reconnect it in Buffer or deselect it in Settings.`;
    }
    return null;
  }

  /** @throws {ApiError} kind "config" when the server has no Buffer token */
  assertConfigured() {
    if (!SERVER.buffer.configured) {
      throw new ApiError(`Buffer is not set up on the server: ${SERVER.buffer.problem}`, {
        kind: 'config', service: 'Buffer', hint: 'Add BUFFER_ACCESS_TOKEN to the .env file next to app.py and restart Poster.'
      });
    }
  }

  /* ---------------------------------------------------------- public API */

  /**
   * Verifies the server's personal access token by reading the account.
   * @param {{signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{id: string, email: string, organizations: Array<{id: string, name: string}>}>}
   */
  async validateBufferToken({ signal, onRetry } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.bufferValidate, {
      json: {}, timeoutMs: API_CONFIG.timeouts.bufferMs, signal, service: 'Buffer', onRetry
    });
    if (!isPlainObject(data.account)) throw new ApiError('Buffer returned unexpected account data.', { kind: 'parse', service: 'Buffer' });
    return data.account;
  }

  /**
   * Validates the token and loads every channel of every organization.
   * @param {{signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{account: object, channels: object[], tokenId: string}>}
   */
  async connectToBuffer({ signal, onRetry } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.bufferConnect, {
      json: {}, timeoutMs: API_CONFIG.timeouts.bufferMs * 3, signal, service: 'Buffer', onRetry
    });
    if (!isPlainObject(data.account) || !Array.isArray(data.channels)) {
      throw new ApiError('Buffer returned an unexpected channel list.', { kind: 'parse', service: 'Buffer' });
    }
    return { account: data.account, channels: data.channels.filter(isPlainObject), tokenId: String(data.tokenId || '') };
  }

  /**
   * Resolves the public URL Buffer should attach. Buffer has no upload
   * endpoint, so the image must be publicly hosted:
   *   1. a URL the user pasted for this image, else
   *   2. a URL from an earlier upload of this image, else
   *   3. the server uploads it to its media host and returns the URL, else
   *   4. null (text-only post) — or an error when images are required.
   * `thumbnailUrl` is set only by a new upload to a host that stores
   * thumbnails (Vercel Blob).
   * @param {object} item
   * @param {{settings?: object, signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{url: string|null, thumbnailUrl: string|null}>}
   */
  async uploadMedia(item, { settings, signal, onRetry } = {}) {
    const s = settings ?? this.getSettings();
    const manual = String(item.mediaUrl || '').trim();
    if (manual) {
      const error = this.validation.validateMediaUrl(manual);
      if (error) throw new ApiError(`"${item.name}": ${error}`, { kind: 'validation', service: 'Buffer' });
      return { url: manual, thumbnailUrl: null };
    }
    if (item.uploadedUrl) return { url: item.uploadedUrl, thumbnailUrl: null };
    if (SERVER.media.configured) {
      const upload = await prepareUploadImage(item);
      const form = new FormData();
      form.append(API_CONFIG.mediaFileField, upload.blob, upload.name);
      const data = await this.api.callWithRetry(API_CONFIG.routes.mediaUpload, {
        form, timeoutMs: API_CONFIG.timeouts.mediaMs, signal, service: 'Media host', onRetry
      });
      if (typeof data.url !== 'string' || !data.url.startsWith('https://')) {
        throw new ApiError('The media host did not return a public https:// URL.', { kind: 'parse', service: 'Media host' });
      }
      const thumbnailUrl = typeof data.thumbnailUrl === 'string' && data.thumbnailUrl.startsWith('https://') ? data.thumbnailUrl : null;
      return { url: data.url, thumbnailUrl };
    }
    if (s.requireMedia) {
      throw new ApiError(`"${item.name}" has no public image URL. Buffer attaches images from public URLs: paste one in the editor, or set BLOB_READ_WRITE_TOKEN (Vercel Blob) or MEDIA_UPLOAD_ENDPOINT on the server.`, { kind: 'validation', service: 'Buffer' });
    }
    return { url: null, thumbnailUrl: null };
  }

  /**
   * Creates one scheduled post per channel (mode "customScheduled").
   * Channels are independent on the server: failures on some are reported
   * while the others still go out. The browser does not retry this call,
   * because a request that reached the server may already have created posts.
   * @param {{text: string, channelIds: string[], dueAt: Date, mediaUrl?: string|null}} post
   * @param {{settings?: object, onRetry?: Function}} [ctx]
   * @returns {Promise<{posts: Array<{channelId: string, postId: string, dueAt: string, status: string}>, failures: Array<{channelId: string, message: string, uncertain: boolean}>}>}
   *   uncertain: the request failed in a way that may still have created the post
   */
  async schedulePost(post, { onRetry } = {}) {
    this.assertConfigured();
    if (!Array.isArray(post.channelIds) || !post.channelIds.length) {
      throw new ApiError('No Buffer channels selected. Choose at least one in Settings → Buffer.', { kind: 'config', service: 'Buffer' });
    }
    if (!(post.dueAt instanceof Date) || Number.isNaN(post.dueAt.getTime()) || post.dueAt.getTime() <= Date.now()) {
      throw new ApiError('The publish time must be a valid future date.', { kind: 'validation', service: 'Buffer' });
    }
    if (!String(post.text || '').trim()) throw new ApiError('The post text is empty.', { kind: 'validation', service: 'Buffer' });
    const channelProblem = this.checkChannels(post.channelIds);
    if (channelProblem) throw new ApiError(channelProblem, { kind: 'config', service: 'Buffer' });

    const data = await this.api.call(API_CONFIG.routes.bufferSchedule, {
      json: { text: post.text, channelIds: post.channelIds, dueAt: post.dueAt.toISOString(), mediaUrl: post.mediaUrl || null },
      timeoutMs: API_CONFIG.timeouts.bufferMs * Math.max(1, post.channelIds.length),
      service: 'Buffer',
      onRetry
    });
    const posts = Array.isArray(data.posts) ? data.posts.filter((p) => isPlainObject(p) && p.postId) : [];
    const failures = Array.isArray(data.failures) ? data.failures.filter(isPlainObject) : [];
    if (!posts.length) throw new ApiError('Buffer did not confirm any scheduled post.', { kind: 'parse', service: 'Buffer' });
    return {
      posts: posts.map((p) => ({ channelId: String(p.channelId), postId: String(p.postId), dueAt: String(p.dueAt || post.dueAt.toISOString()), status: String(p.status || 'scheduled') })),
      failures: failures.map((f) => ({ channelId: String(f.channelId), message: String(f.message || 'Unknown error'), uncertain: f.uncertain === true }))
    };
  }

  /**
   * One page of the account's posts, read straight from Buffer.
   * @param {{filter: 'all'|'upcoming'|'published', after?: Object<string, string>|null}} query
   *   after: the previous page's `next`, for the page after it
   * @returns {Promise<{posts: BufferPost[], next: Object<string, string>|null}>}
   */
  async listPosts({ filter, after = null }) {
    this.assertConfigured();
    // Organizations known from connecting save the server a Buffer request.
    const organizationIds = (this.getConnection()?.account?.organizations || []).map((o) => o.id).filter(Boolean);
    const data = await this.api.call(API_CONFIG.routes.bufferPosts, {
      json: { filter, after, organizationIds },
      timeoutMs: API_CONFIG.timeouts.bufferMs * Math.max(2, organizationIds.length), service: 'Buffer'
    });
    const { posts, next } = normalizePostResults(data);
    return { posts, next };
  }

  /**
   * Changes the text and/or publish time of posts. The server skips posts
   * that are published by now.
   * @param {string[]} postIds @param {{text?: string, dueAt?: Date}} changes
   */
  async editPosts(postIds, { text, dueAt }) {
    return this.#changePosts(API_CONFIG.routes.bufferEditPosts, postIds, { text: text ?? null, dueAt: dueAt ? dueAt.toISOString() : null });
  }

  /**
   * Deletes posts from Buffer. The server skips posts that are published by now.
   * @param {string[]} postIds
   */
  async deletePosts(postIds) {
    return this.#changePosts(API_CONFIG.routes.bufferDeletePosts, postIds, {});
  }

  async #changePosts(route, postIds, fields) {
    this.assertConfigured();
    // The server reads each post before changing it: two Buffer calls per post.
    const data = await this.api.call(route, {
      json: { postIds, ...fields }, timeoutMs: API_CONFIG.timeouts.bufferMs * 2 * Math.max(1, postIds.length), service: 'Buffer'
    });
    return normalizePostResults(data);
  }
}
