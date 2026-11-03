/** PostsFeed: the Scheduled posts card, read from the server's post queue. */
import { ApiError } from './errors.js';
import { truncate } from './util.js';
import { POST_FILTERS, sortPosts } from './services/social.js';

/**
 * Posts as the server's queue has them, kept in memory only. The list loads
 * at start, on Refresh, when the filter or mode changes and after scheduling;
 * edits and deletes update it from the server's own answer instead of
 * reloading. Emits "change" whenever there is something new to render.
 */
export class PostsFeed extends EventTarget {
  constructor({ notifier, store, getSocial }) {
    super();
    this.notifier = notifier;
    this.store = store;
    this.getSocial = getSocial;
    /** @type {'all'|'upcoming'|'published'} */
    this.filter = 'all';
    /** @type {SocialPost[]} */
    this.posts = [];
    /** @type {string|null} cursor for the next page */
    this.next = null;
    /** @type {{lastRunAt: string|null}|null} when the server's publisher last ran (null in Demo mode) */
    this.publisher = null;
    /** @type {false|'all'|'more'} */
    this.loading = false;
    /** @type {ApiError|null} why the last load failed */
    this.error = null;
    /** @type {{at: number, demo: boolean}|null} when the list was last read from the server */
    this.loaded = null;
    /** @type {Set<string>} posts with an edit or delete in flight */
    this.busy = new Set();
    this.request = 0;
    /** @type {Promise<object>|null} the list request in flight */
    this.inFlight = null;
  }

  /** @param {'all'|'upcoming'|'published'} filter */
  setFilter(filter) {
    if (!(filter in POST_FILTERS) || filter === this.filter) return;
    this.filter = filter;
    this.load();
  }

  /**
   * Reads the first page (or, with `more`, the next one) from the server. A
   * newer load supersedes one still in flight, but waits for it: a request the
   * browser gave up on still runs on the server. Quick filter changes
   * therefore cost one more request at most.
   * @param {{more?: boolean}} [options]
   */
  async load({ more = false } = {}) {
    if (more && (!this.next || this.loading)) return;
    const request = ++this.request;
    this.loading = more ? 'more' : 'all';
    this.error = null;
    this.#emit();
    while (this.inFlight) {
      await this.inFlight.catch(() => {});
      if (request !== this.request) return;  // a newer load takes over
    }
    const social = this.getSocial();
    const pending = social.listPosts({ filter: this.filter, after: more ? this.next : null });
    this.inFlight = pending;
    try {
      const page = await pending;
      if (request !== this.request) return;
      const known = new Set(more ? this.posts.map((p) => p.postId) : []);
      this.posts = sortPosts([...(more ? this.posts : []), ...page.posts.filter((p) => !known.has(p.postId))], this.filter);
      this.next = page.next;
      if (!more) this.publisher = page.publisher || null;
      this.loaded = { at: Date.now(), demo: social.isDemo };
    } catch (error) {
      if (request !== this.request) return;
      this.error = error;
      if (!more) {
        this.posts = [];
        this.next = null;
        this.publisher = null;
        this.loaded = null;
      }
      // A missing server setup is already reported by the startup banner.
      if (error.kind !== 'config') this.notifier.log('error', 'schedule', `Could not load the scheduled posts: ${error.message}`, error.hint || '');
    } finally {
      if (this.inFlight === pending) this.inFlight = null;
      if (request === this.request) {
        this.loading = false;
        this.#emit();
      }
    }
  }

  /** @param {string} postId @returns {SocialPost|null} */
  get(postId) {
    return this.posts.find((p) => p.postId === postId) || null;
  }

  /** @param {SocialPost} post @returns {boolean} whether it can still be edited or deleted */
  static canChange(post) {
    return !post.published;
  }

  /**
   * Changes a post's text and/or publish time in the queue, and shows the
   * post as the server returns it. Throws when the server did not change it.
   * A failed post goes back in the queue.
   * @param {string} postId @param {{text?: string, dueAt?: Date}} changes
   */
  async edit(postId, changes) {
    const result = await this.#change(postId, (social) => social.editPosts([postId], changes));
    const updated = result.posts.find((p) => p.postId === postId);
    if (!updated) throw this.#failure(result, 'The post was not changed');
    this.#replace(postId, updated);
    // The batch mirrors the queue: the image's card and editor show the post as it is now.
    const patch = { edited: true };
    if (changes.dueAt) patch.dueAt = updated.dueAt || changes.dueAt.toISOString();
    if (changes.text !== undefined) patch.text = updated.text || changes.text;
    this.store.updateResultsForPosts([postId], patch);
    const what = [changes.text !== undefined && 'text', changes.dueAt && 'publish time'].filter(Boolean).join(' and ');
    this.notifier.log('success', 'schedule', `${this.#label(updated)}: changed the ${what}${this.getSocial().isDemo ? ' in Demo mode (nothing is published)' : ''}.`);
    return updated;
  }

  /**
   * Deletes a post from the queue and removes it from the list. Its image can
   * be scheduled again. Throws when the server did not delete it.
   * @param {string} postId
   */
  async remove(postId) {
    const post = this.get(postId);
    const result = await this.#change(postId, (social) => social.deletePosts([postId]));
    if (!result.deleted.includes(postId)) throw this.#failure(result, 'The post was not deleted');
    this.posts = this.posts.filter((p) => p.postId !== postId);
    this.store.updateResultsForPosts([postId], null);
    this.notifier.log('success', 'schedule', `${post ? this.#label(post) : 'Post'}: deleted${this.getSocial().isDemo ? ' in Demo mode' : ' from the queue'}.`);
    this.#emit();
  }

  async #change(postId, request) {
    this.busy.add(postId);
    this.#emit();
    try {
      const result = await request(this.getSocial());
      // Learn from refusals: a post published or deleted in the meantime.
      for (const failure of result.failures) {
        if (failure.reason === 'missing') this.posts = this.posts.filter((p) => p.postId !== failure.postId);
        if (failure.reason === 'published') {
          const post = this.get(failure.postId);
          if (post) this.#replace(post.postId, { ...post, status: 'sent', published: true });
        }
      }
      return result;
    } finally {
      this.busy.delete(postId);
      this.#emit();
    }
  }

  #replace(postId, post) {
    this.posts = this.posts.map((p) => (p.postId === postId ? post : p));
    this.#emit();
  }

  #failure(result, prefix) {
    const failure = result.failures[0];
    const error = new ApiError(failure ? `${prefix}: ${failure.message}` : `${prefix}.`, { kind: failure?.reason === 'published' ? 'validation' : 'client', service: 'Meta' });
    this.notifier.log('warn', 'schedule', error.message);
    return error;
  }

  /** @param {SocialPost} post */
  #label(post) {
    return `${this.loaded?.demo ? '[Demo] ' : ''}${post.channelName || 'Post'} post "${truncate(post.text.replace(/\s+/g, ' '), 40)}"`;
  }

  #emit() {
    this.dispatchEvent(new CustomEvent('change'));
  }
}
