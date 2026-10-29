/** BufferPostsFeed: the Scheduled posts card, read straight from Buffer. */
import { ApiError } from './errors.js';
import { truncate } from './util.js';
import { POST_FILTERS, sortPosts } from './services/buffer.js';

/**
 * Posts as Buffer has them, kept in memory only. Buffer's API allows few
 * requests (100 per 15 minutes on the free plan), so the list loads at start,
 * on Refresh, when the filter or mode changes and after scheduling; edits and
 * deletes update it from Buffer's own answer instead of reloading. Emits
 * "change" whenever there is something new to render.
 */
export class BufferPostsFeed extends EventTarget {
  constructor({ notifier, store, getBuffer }) {
    super();
    this.notifier = notifier;
    this.store = store;
    this.getBuffer = getBuffer;
    /** @type {'all'|'upcoming'|'published'} */
    this.filter = 'all';
    /** @type {BufferPost[]} */
    this.posts = [];
    /** @type {Object<string, string>|null} cursor per organization for the next page */
    this.next = null;
    /** @type {false|'all'|'more'} */
    this.loading = false;
    /** @type {ApiError|null} why the last load failed */
    this.error = null;
    /** @type {{at: number, demo: boolean}|null} when the list was last read from Buffer */
    this.loaded = null;
    /** @type {Set<string>} posts with an edit or delete in flight */
    this.busy = new Set();
    this.request = 0;
    /** @type {Promise<object>|null} the Buffer request in flight */
    this.inFlight = null;
  }

  /** @param {'all'|'upcoming'|'published'} filter */
  setFilter(filter) {
    if (!(filter in POST_FILTERS) || filter === this.filter) return;
    this.filter = filter;
    this.load();
  }

  /**
   * Reads the first page (or, with `more`, the next one) from Buffer. A newer
   * load supersedes one still in flight, but waits for it: Buffer allows few
   * requests, and a request the browser gave up on still runs on the server.
   * Quick filter changes therefore cost one more request at most.
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
    const buffer = this.getBuffer();
    const pending = buffer.listPosts({ filter: this.filter, after: more ? this.next : null });
    this.inFlight = pending;
    try {
      const page = await pending;
      if (request !== this.request) return;
      const known = new Set(more ? this.posts.map((p) => p.postId) : []);
      this.posts = sortPosts([...(more ? this.posts : []), ...page.posts.filter((p) => !known.has(p.postId))], this.filter);
      this.next = page.next;
      this.loaded = { at: Date.now(), demo: buffer.isDemo };
    } catch (error) {
      if (request !== this.request) return;
      this.error = error;
      if (!more) {
        this.posts = [];
        this.next = null;
        this.loaded = null;
      }
      // A missing Buffer setup is already reported by the startup banner.
      if (error.kind !== 'config') this.notifier.log('error', 'schedule', `Could not load posts from Buffer: ${error.message}`, error.hint || '');
    } finally {
      if (this.inFlight === pending) this.inFlight = null;
      if (request === this.request) {
        this.loading = false;
        this.#emit();
      }
    }
  }

  /** @param {string} postId @returns {BufferPost|null} */
  get(postId) {
    return this.posts.find((p) => p.postId === postId) || null;
  }

  /** @param {BufferPost} post @returns {boolean} whether it can still be edited or deleted */
  static canChange(post) {
    return !post.published;
  }

  /**
   * Changes a post's text and/or publish time in Buffer, and shows the post as
   * Buffer returns it. Throws when Buffer did not change it.
   * @param {string} postId @param {{text?: string, dueAt?: Date}} changes
   */
  async edit(postId, changes) {
    const result = await this.#change(postId, (buffer) => buffer.editPosts([postId], changes));
    const updated = result.posts.find((p) => p.postId === postId);
    if (!updated) throw this.#failure(result, 'The post was not changed');
    this.#replace(postId, updated);
    // The batch mirrors Buffer: the image's card and editor show the post as it is now.
    const patch = { edited: true };
    if (changes.dueAt) patch.dueAt = updated.dueAt || changes.dueAt.toISOString();
    if (changes.text !== undefined) patch.text = updated.text || changes.text;
    this.store.updateResultsForPosts([postId], patch);
    const what = [changes.text !== undefined && 'text', changes.dueAt && 'publish time'].filter(Boolean).join(' and ');
    this.notifier.log('success', 'schedule', `${this.#label(updated)}: changed the ${what} ${this.getBuffer().isDemo ? 'in Demo mode (not in Buffer)' : 'in Buffer'}.`);
    return updated;
  }

  /**
   * Deletes a post from Buffer and removes it from the list. Its image can be
   * scheduled again. Throws when Buffer did not delete it.
   * @param {string} postId
   */
  async remove(postId) {
    const post = this.get(postId);
    const result = await this.#change(postId, (buffer) => buffer.deletePosts([postId]));
    if (!result.deleted.includes(postId)) throw this.#failure(result, 'The post was not deleted');
    this.posts = this.posts.filter((p) => p.postId !== postId);
    this.store.updateResultsForPosts([postId], null);
    this.notifier.log('success', 'schedule', `${post ? this.#label(post) : 'Post'}: deleted ${this.getBuffer().isDemo ? 'in Demo mode (not in Buffer)' : 'from Buffer'}.`);
    this.#emit();
  }

  async #change(postId, request) {
    this.busy.add(postId);
    this.#emit();
    try {
      const result = await request(this.getBuffer());
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
    const error = new ApiError(failure ? `${prefix}: ${failure.message}` : `${prefix}.`, { kind: failure?.reason === 'published' ? 'validation' : 'client', service: 'Buffer' });
    this.notifier.log('warn', 'schedule', error.message);
    return error;
  }

  /** @param {BufferPost} post */
  #label(post) {
    return `${this.loaded?.demo ? '[Demo] ' : ''}${post.channelName || 'Post'} post "${truncate(post.text.replace(/\s+/g, ' '), 40)}"`;
  }

  #emit() {
    this.dispatchEvent(new CustomEvent('change'));
  }
}
