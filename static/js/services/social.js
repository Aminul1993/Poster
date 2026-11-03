/** SocialService: Facebook and Instagram posts, queued and published by the Poster server. */
import { API_CONFIG, RULES, SERVER, STORAGE_KEYS } from '../config.js';
import { ApiError } from '../errors.js';
import { prepareUploadImage } from '../image.js';
import { isPlainObject, truncate } from '../util.js';

/** Display names and colours for the networks Poster publishes to. */
export const SERVICE_META = Object.freeze({
  instagram: ['Instagram', '#d6246e'], facebook: ['Facebook', '#1877f2']
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

/** Instagram's limits for a feed post, which the server enforces per channel. */
export const INSTAGRAM = RULES.instagram || { maxCaptionChars: 2200, maxHashtags: 30 };

const HASHTAG_RE = /(?<![\p{L}\p{N}_])#[\p{L}\p{N}\p{M}_]+/gu;

/**
 * Why Instagram would refuse a post, or null. The server refuses the same posts on Instagram channels
 * (and still queues them on Facebook).
 * @param {string} text the full post text @param {boolean} hasImage @returns {string|null}
 */
export function instagramProblem(text, hasImage) {
  if (!hasImage) return 'Instagram posts need an image.';
  if ([...String(text)].length > INSTAGRAM.maxCaptionChars) return `Instagram captions are limited to ${INSTAGRAM.maxCaptionChars.toLocaleString('en')} characters.`;
  const tags = new Set(String(text).toLowerCase().match(HASHTAG_RE) || []);
  if (tags.size > INSTAGRAM.maxHashtags) return `Instagram allows at most ${INSTAGRAM.maxHashtags} hashtags per post.`;
  return null;
}

/**
 * A post as the posts list shows it, read from the server's queue. Status is scheduled (waiting for its
 * time), sending (being published), sent (published) or error (the publisher gave up; it can be edited to
 * try again, or deleted).
 * @typedef {{postId: string, status: string, published: boolean, dueAt: string|null, sentAt: string|null, createdAt: string|null,
 *            text: string, channelId: string, channelName: string, service: string, thumbnail: string|null,
 *            externalLink: string|null, error: string|null, attempts: number}} SocialPost
 */

/** @param {object} p @returns {SocialPost} */
export function normalizePost(p) {
  const text = (value) => (typeof value === 'string' && value ? value : null);
  const https = (value) => (typeof value === 'string' && value.startsWith('https://') ? value : null);
  return {
    postId: String(p.postId), status: String(p.status || ''), published: PUBLISHED_STATUSES.includes(p.status),
    dueAt: text(p.dueAt), sentAt: text(p.sentAt), createdAt: text(p.createdAt), text: String(p.text ?? ''),
    channelId: String(p.channelId || ''), channelName: String(p.channelName || ''), service: String(p.service || ''),
    // Demo posts keep the browser-made thumbnail as a data: URL.
    thumbnail: https(p.thumbnail) || (String(p.thumbnail || '').startsWith('data:image/') ? p.thumbnail : null),
    externalLink: https(p.externalLink), error: text(p.error), attempts: Number(p.attempts) || 0
  };
}

/**
 * Sorts posts the way the server does for a filter: upcoming soonest first,
 * everything else newest first.
 * @param {SocialPost[]} posts @param {string} filter @returns {SocialPost[]}
 */
export function sortPosts(posts, filter) {
  const key = (p) => p.dueAt || p.createdAt || '';
  const direction = filter === 'upcoming' ? 1 : -1;
  return [...posts].sort((a, b) => direction * key(a).localeCompare(key(b)));
}

/**
 * Checks the server's answer to listing, editing or deleting posts.
 * @param {object} data
 * @returns {{posts: SocialPost[], next: string|null, deleted: string[], publisher: {lastRunAt: string|null}|null,
 *            failures: Array<{postId: string, message: string, reason: 'published'|'missing'|'error'}>}}
 */
export function normalizePostResults(data) {
  const posts = Array.isArray(data.posts) ? data.posts.filter((p) => isPlainObject(p) && p.postId) : [];
  const failures = Array.isArray(data.failures) ? data.failures.filter((f) => isPlainObject(f) && f.postId) : [];
  return {
    posts: posts.map(normalizePost),
    next: typeof data.next === 'string' && data.next ? data.next : null,
    deleted: Array.isArray(data.deleted) ? data.deleted.map(String) : [],
    publisher: isPlainObject(data.publisher)
      ? { lastRunAt: typeof data.publisher.lastRunAt === 'string' && data.publisher.lastRunAt ? data.publisher.lastRunAt : null }
      : null,
    failures: failures.map((f) => ({
      postId: String(f.postId), message: String(f.message || 'Unknown error'),
      reason: ['published', 'missing'].includes(f.reason) ? f.reason : 'error'
    }))
  };
}

/**
 * Facebook Pages and Instagram accounts through the Poster server. The server
 * holds the Meta access token, keeps the queue of scheduled posts and
 * publishes each one at its time; the browser caches the channels (keyed by a
 * fingerprint of the server's token) so the channel picker works without
 * reconnecting.
 */
export class SocialService {
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
    if (!SERVER.social.tokenId || live.tokenId !== SERVER.social.tokenId) return null;
    return live;
  }

  /** @param {{account: object, channels: object[], tokenId?: string}} connection */
  saveConnection(connection) {
    this.storage.set(STORAGE_KEYS.connection, {
      live: {
        account: connection.account,
        channels: connection.channels,
        connectedAt: new Date().toISOString(),
        tokenId: connection.tokenId || SERVER.social.tokenId
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

  /** @param {object} [settings] @returns {boolean} whether an Instagram account is among the selected channels */
  hasInstagram(settings = this.getSettings()) {
    const known = this.channels(settings);
    return this.selectedChannelIds(settings).some((id) => known.find((c) => c.id === id)?.service === 'instagram');
  }

  /**
   * Detects selected channels that are unknown or that the token cannot post to.
   * @param {string[]} ids @returns {string|null} problem description
   */
  checkChannels(ids) {
    const known = this.channels();
    if (!known.length) return null;
    for (const id of ids) {
      const channel = known.find((c) => c.id === id);
      if (!channel) return `A selected channel (${truncate(id, 14)}) is no longer available to the server's Meta token. Reconnect in Settings → Facebook & Instagram.`;
      if (!channel.usable) return `${channel.name} (${serviceMeta(channel.service).label}): ${channel.problem || 'the token cannot post here.'} Fix it in Meta Business settings or deselect it in Settings.`;
    }
    return null;
  }

  /** @throws {ApiError} kind "config" when the server has no Meta token or no database */
  assertConfigured() {
    if (!SERVER.social.configured) {
      throw new ApiError(`Facebook and Instagram are not set up on the server: ${SERVER.social.problem}`, {
        kind: 'config', service: 'Meta', hint: 'Add META_ACCESS_TOKEN (and, on Vercel, a Postgres DATABASE_URL) to the server configuration and restart Poster.'
      });
    }
  }

  /**
   * The posts list reads and changes the server's queue alone, so it works with a database even before
   * the Meta token is set.
   * @throws {ApiError} kind "config" when the server has no database
   */
  assertDatabase() {
    const database = SERVER.social.database;
    if (!database?.configured) {
      throw new ApiError(`The server has no database for scheduled posts: ${database?.problem || 'not configured.'}`, {
        kind: 'config', service: 'Database', hint: 'Set DATABASE_URL (or POSTGRES_URL) in the server configuration and restart Poster.'
      });
    }
  }

  /* ---------------------------------------------------------- public API */

  /**
   * Verifies the server's Meta access token by reading who it belongs to.
   * @param {{signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{id: string, name: string}>}
   */
  async validateToken({ signal, onRetry } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.socialValidate, {
      json: {}, timeoutMs: API_CONFIG.timeouts.socialMs, signal, service: 'Meta', onRetry
    });
    if (!isPlainObject(data.account)) throw new ApiError('Meta returned unexpected account data.', { kind: 'parse', service: 'Meta' });
    return data.account;
  }

  /**
   * Validates the token and loads every Page it manages, each followed by its linked Instagram account.
   * @param {{signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{account: object, channels: object[], tokenId: string}>}
   */
  async connectAccounts({ signal, onRetry } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.socialConnect, {
      json: {}, timeoutMs: API_CONFIG.timeouts.socialMs * 2, signal, service: 'Meta', onRetry
    });
    if (!isPlainObject(data.account) || !Array.isArray(data.channels)) {
      throw new ApiError('Meta returned an unexpected channel list.', { kind: 'parse', service: 'Meta' });
    }
    return { account: data.account, channels: data.channels.filter(isPlainObject), tokenId: String(data.tokenId || '') };
  }

  /**
   * Resolves the public URL Facebook and Instagram fetch the image from. The
   * image must be publicly hosted:
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
      if (error) throw new ApiError(`"${item.name}": ${error}`, { kind: 'validation', service: 'Meta' });
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
    if (s.requireMedia || this.hasInstagram(s)) {
      throw new ApiError(`"${item.name}" has no public image URL. Facebook and Instagram fetch images from public URLs: paste one in the editor, or set BLOB_READ_WRITE_TOKEN (Vercel Blob) or MEDIA_UPLOAD_ENDPOINT on the server.`, { kind: 'validation', service: 'Meta' });
    }
    return { url: null, thumbnailUrl: null };
  }

  /**
   * Queues one post per channel; the server's publisher publishes each at
   * its time. Channels are independent on the server: the ones that cannot
   * take the post are reported while the others are queued. Sending the same
   * `requestKey` again returns the posts already queued for it, so a request
   * whose answer was lost can safely be sent again.
   * @param {{text: string, channelIds: string[], dueAt: Date, mediaUrl?: string|null, requestKey: string}} post
   * @param {{settings?: object, onRetry?: Function}} [ctx]
   * @returns {Promise<{posts: Array<{channelId: string, postId: string, dueAt: string, status: string}>, failures: Array<{channelId: string, message: string, uncertain: boolean}>}>}
   */
  async schedulePost(post, { onRetry } = {}) {
    this.assertConfigured();
    if (!Array.isArray(post.channelIds) || !post.channelIds.length) {
      throw new ApiError('No channels selected. Choose at least one in Settings → Facebook & Instagram.', { kind: 'config', service: 'Meta' });
    }
    if (!(post.dueAt instanceof Date) || Number.isNaN(post.dueAt.getTime()) || post.dueAt.getTime() <= Date.now()) {
      throw new ApiError('The publish time must be a valid future date.', { kind: 'validation', service: 'Meta' });
    }
    if (!String(post.text || '').trim()) throw new ApiError('The post text is empty.', { kind: 'validation', service: 'Meta' });
    const channelProblem = this.checkChannels(post.channelIds);
    if (channelProblem) throw new ApiError(channelProblem, { kind: 'config', service: 'Meta' });

    const data = await this.api.call(API_CONFIG.routes.socialSchedule, {
      json: { text: post.text, channelIds: post.channelIds, dueAt: post.dueAt.toISOString(), mediaUrl: post.mediaUrl || null, requestKey: post.requestKey || null },
      timeoutMs: API_CONFIG.timeouts.socialMs,
      service: 'Meta',
      onRetry
    });
    const posts = Array.isArray(data.posts) ? data.posts.filter((p) => isPlainObject(p) && p.postId) : [];
    const failures = Array.isArray(data.failures) ? data.failures.filter(isPlainObject) : [];
    if (!posts.length) throw new ApiError('The server did not confirm any scheduled post.', { kind: 'parse', service: 'Meta' });
    return {
      posts: posts.map((p) => ({ channelId: String(p.channelId), postId: String(p.postId), dueAt: String(p.dueAt || post.dueAt.toISOString()), status: String(p.status || 'scheduled') })),
      failures: failures.map((f) => ({ channelId: String(f.channelId), message: String(f.message || 'Unknown error'), uncertain: f.uncertain === true }))
    };
  }

  /**
   * One page of the queued and published posts.
   * @param {{filter: 'all'|'upcoming'|'published', after?: string|null}} query
   *   after: the previous page's `next`, for the page after it
   * @returns {Promise<{posts: SocialPost[], next: string|null, publisher: {lastRunAt: string|null}|null}>}
   */
  async listPosts({ filter, after = null }) {
    this.assertDatabase();
    const data = await this.api.call(API_CONFIG.routes.socialPosts, {
      json: { filter, after }, timeoutMs: API_CONFIG.timeouts.socialMs, service: 'Meta'
    });
    const { posts, next, publisher } = normalizePostResults(data);
    return { posts, next, publisher };
  }

  /**
   * Changes the text and/or publish time of posts. The server skips posts
   * that are published (or being published) by now; a failed post goes back
   * in the queue.
   * @param {string[]} postIds @param {{text?: string, dueAt?: Date}} changes
   */
  async editPosts(postIds, { text, dueAt }) {
    return this.#changePosts(API_CONFIG.routes.socialEditPosts, postIds, { text: text ?? null, dueAt: dueAt ? dueAt.toISOString() : null });
  }

  /**
   * Deletes posts from the queue. The server skips posts that are published by now.
   * @param {string[]} postIds
   */
  async deletePosts(postIds) {
    return this.#changePosts(API_CONFIG.routes.socialDeletePosts, postIds, {});
  }

  async #changePosts(route, postIds, fields) {
    this.assertDatabase();
    const data = await this.api.call(route, {
      json: { postIds, ...fields }, timeoutMs: API_CONFIG.timeouts.socialMs, service: 'Meta'
    });
    return normalizePostResults(data);
  }
}
