/** SchedulerService: bulk operations, progress and cancellation. */
import { STORAGE_KEYS } from './config.js';
import { ApiError, isCancelled, throwIfAborted } from './errors.js';
import { countedResult, emptyContent, hasContent } from './store.js';
import { TimeZoneUtil } from './time.js';
import { clamp, pluralize, uid } from './util.js';

/**
 * @typedef {Object} Operation
 * @property {string} id
 * @property {'generate'|'schedule'} kind
 * @property {string} label
 * @property {number} total
 * @property {number} done
 * @property {number} succeeded
 * @property {number} failed
 * @property {number} skipped
 * @property {number} cancelled
 * @property {number} retries
 * @property {number} startedAt
 * @property {number|null} finishedAt
 * @property {string[]} active names currently being processed
 * @property {Array<{name: string, message: string}>} errors
 * @property {ApiError|null} abortReason fatal error that stopped the run
 * @property {boolean} cancelRequested
 * @property {number[]} dueTimes publish times (ms) of the posts a scheduling run created
 * @property {string[]} postIds post IDs a scheduling run queued
 * @property {boolean} demo whether a scheduling run went to Demo mode's simulated queue
 * @property {string} [state] done | partial | failed | cancelled
 */
export class SchedulerService extends EventTarget {
  constructor({ storage, notifier, validation, store, getSettings, getGroq, getSocial }) {
    super();
    this.storage = storage;
    this.notifier = notifier;
    this.validation = validation;
    this.store = store;
    this.getSettings = getSettings;
    this.getGroq = getGroq;
    this.getSocial = getSocial;
    /** @type {Operation|null} */
    this.current = null;
    // Scheduled posts are read from the server's queue now; drop the list older versions kept in the browser.
    storage.remove(STORAGE_KEYS.schedules);
  }

  /** @returns {boolean} */
  get isRunning() { return Boolean(this.current); }

  /**
   * Runs `worker` over `items` with bounded concurrency. Cancelling aborts
   * in-flight requests where that is safe; a fatal error (auth, offline,
   * unreachable host,
   * configuration) stops the remaining items automatically.
   * @param {{kind: 'generate'|'schedule', label: string, items: object[], concurrency?: number,
   *          worker: (item: object, ctx: {signal: AbortSignal, onRetry: Function}) => Promise<string>}} options
   * @returns {Promise<Operation>}
   */
  async run({ kind, label, items, concurrency = 1, worker }) {
    if (this.current) throw new ApiError('Another operation is already running. Wait for it to finish or cancel it.', { kind: 'validation' });
    const controller = new AbortController();
    /** @type {Operation} */
    const op = {
      id: uid('op'), kind, label, total: items.length, done: 0, succeeded: 0, failed: 0, skipped: 0, cancelled: 0, retries: 0,
      startedAt: Date.now(), finishedAt: null, active: [], errors: [], abortReason: null, cancelRequested: false,
      dueTimes: [], postIds: [], demo: false
    };
    this.current = op;
    this.controller = controller;
    for (const item of items) this.store.update(item.id, { busy: 'queued' }, { reason: 'status', persist: false });
    this.#emit('start', op);

    const queue = [...items];
    const runNext = async () => {
      while (queue.length && !controller.signal.aborted) {
        const item = queue.shift();
        if (!this.store.get(item.id)) { op.skipped += 1; op.done += 1; continue; }
        op.active.push(item.name);
        this.#emit('progress', op);
        try {
          const outcome = await worker(item, { signal: controller.signal, onRetry: this.#retryLogger(op, item) });
          if (outcome === 'skipped') op.skipped += 1; else op.succeeded += 1;
        } catch (error) {
          if (isCancelled(error)) {
            op.cancelled += 1;
          } else {
            op.failed += 1;
            op.errors.push({ name: item.name, message: error?.message || String(error) });
            if (error instanceof ApiError && error.fatal && !controller.signal.aborted) {
              op.abortReason = error;
              controller.abort();
            }
          }
        } finally {
          op.done += 1;
          const index = op.active.indexOf(item.name);
          if (index !== -1) op.active.splice(index, 1);
          this.#emit('progress', op);
        }
      }
    };
    await Promise.all(Array.from({ length: clamp(concurrency, 1, Math.max(1, items.length)) }, () => runNext()));

    for (const item of queue) {
      op.cancelled += 1;
      this.store.update(item.id, { busy: null }, { reason: 'status', persist: false });
    }
    op.finishedAt = Date.now();
    if (op.abortReason) op.state = 'failed';
    else if (op.cancelRequested) op.state = 'cancelled';
    else if (op.failed && op.succeeded) op.state = 'partial';
    else if (op.failed) op.state = 'failed';
    else op.state = 'done';
    this.current = null;
    this.controller = null;
    this.#emit('complete', op);
    return op;
  }

  /**
   * Requests cancellation of the running operation.
   * @returns {boolean} false when nothing is running
   */
  cancel() {
    const op = this.current;
    if (!op || op.cancelRequested) return false;
    op.cancelRequested = true;
    this.controller?.abort();
    this.notifier.log('warn', 'user', `Cancelled: ${op.label}.`);
    this.#emit('progress', op);
    return true;
  }

  #retryLogger(op, item) {
    return ({ attempt, maxRetries, delayMs, error, server }) => {
      op.retries += 1;
      const who = server ? 'Server retry' : 'Retry';
      this.notifier.log('warn', 'retry', `${who} ${attempt}/${maxRetries} for "${item.name}" after ${delayMs / 1000}s: ${error.message}`);
      this.#emit('progress', op);
    };
  }

  /**
   * Worker: generates content for one image.
   * @param {object} item @param {{signal: AbortSignal, onRetry: Function}} ctx
   * @returns {Promise<string>}
   */
  async generateItem(item, { signal, onRetry }) {
    const settings = this.getSettings();
    const groq = this.getGroq();
    const previous = { error: item.error, errorHint: item.errorHint, errorPhase: item.errorPhase };
    this.store.update(item.id, { busy: 'generating', error: null, errorHint: '', errorPhase: null }, { reason: 'status', persist: false });
    try {
      const result = await groq.generateContent(item, { signal, onRetry, settings });
      const replaced = hasContent(item) ? item.content : item.previousContent;
      this.store.update(item.id, {
        busy: null, content: result.content, generatedContent: result.content, previousContent: replaced, result: null, generatedAt: new Date().toISOString(), showErrors: false
      }, { reason: 'generated' });
      const c = result.content;
      this.notifier.log('success', 'ai', `Generated content for "${item.name}": ${c.category}, ${pluralize(c.hashtags.length, 'hashtag')}, estimated engagement ${c.engagement_score}/100.`, [result.repaired ? 'Recovered from a malformed first reply.' : '', ...result.issues].filter(Boolean).join(' '));
      return 'done';
    } catch (error) {
      if (isCancelled(error)) {
        this.store.update(item.id, { busy: null, ...previous }, { reason: 'status', persist: false });
        throw error;
      }
      const message = error?.message || String(error);
      this.store.update(item.id, { busy: null, error: message, errorHint: error?.hint || '', errorPhase: 'generate' }, { reason: 'status', persist: false });
      this.notifier.log('error', 'ai', `Generation failed for "${item.name}": ${message}`, error?.hint || error?.details || '');
      throw error;
    }
  }

  /**
   * Worker: validates, attaches media and schedules one post.
   * Once the post request is sent it is allowed to finish even if the user
   * cancels, so the batch and the queue never disagree about what was scheduled.
   * @param {object} item
   * @param {{signal: AbortSignal, onRetry: Function}} ctx
   * @param {{skipScheduled?: boolean}} [options]
   * @returns {Promise<string>} "done" or "skipped"
   */
  async scheduleItem(item, { signal, onRetry }, { skipScheduled = false } = {}) {
    throwIfAborted(signal);
    const settings = this.getSettings();
    const social = this.getSocial();
    if (skipScheduled && countedResult(item, settings.mode)) {
      this.store.update(item.id, { busy: null }, { reason: 'status', persist: false });
      this.notifier.log('info', 'schedule', `Skipped "${item.name}": already scheduled.`);
      return 'skipped';
    }
    const check = this.validation.validatePost(item, settings);
    if (!check.valid) {
      this.store.update(item.id, { busy: null, error: check.message, errorHint: '', errorPhase: 'schedule', showErrors: true }, { reason: 'status', persist: false });
      this.notifier.log('error', 'schedule', `Not scheduled "${item.name}": ${check.message}`);
      throw new ApiError(check.message, { kind: 'validation' });
    }
    const channelIds = social.selectedChannelIds(settings);
    if (!channelIds.length) {
      this.store.update(item.id, { busy: null }, { reason: 'status', persist: false });
      throw new ApiError('No channels selected. Choose at least one in Settings → Facebook & Instagram.', { kind: 'config', service: 'Meta' });
    }

    const previous = { error: item.error, errorHint: item.errorHint, errorPhase: item.errorPhase };
    this.store.update(item.id, { busy: 'scheduling', error: null, errorHint: '', errorPhase: null }, { reason: 'status', persist: false });
    try {
      const { url: mediaUrl, thumbnailUrl } = await social.uploadMedia(item, { settings, signal, onRetry });
      throwIfAborted(signal);
      if (mediaUrl && !item.mediaUrl && mediaUrl !== item.uploadedUrl) {
        // Simulated Demo URLs are never remembered, so Live mode cannot reuse them.
        if (!social.isDemo) this.store.update(item.id, { uploadedUrl: mediaUrl, uploadedThumbUrl: thumbnailUrl || '' }, { reason: 'status' });
      } else if (!mediaUrl) {
        this.notifier.log('warn', 'schedule', `"${item.name}" will be posted as text only (no public image URL).`);
      }

      const text = this.composeText(item);
      // Demo mode shows the thumbnail in its simulated posts list; the server ignores it.
      const listThumbnail = thumbnailUrl || item.uploadedThumbUrl || item.thumbDataUrl;
      const result = await this.createPosts(social, { text, channelIds, dueAt: check.date, mediaUrl, thumbnailUrl: listThumbnail }, { settings, onRetry });
      const postIds = result.posts.map((p) => p.postId);
      const failures = result.failures.map((f) => ({ id: f.channelId, name: social.channelName(f.channelId, settings), message: f.message }));
      this.store.update(item.id, {
        busy: null, error: null, errorHint: '', errorPhase: null, showErrors: false,
        result: {
          text, dueAt: check.date.toISOString(), timezone: settings.timezone, mode: social.isDemo ? 'demo' : 'live', postIds,
          channels: result.posts.map((p) => social.channelName(p.channelId, settings)), partial: failures.length > 0, failures
        }
      }, { reason: 'scheduled' });
      if (this.current) {
        this.current.dueTimes.push(check.date.getTime());
        this.current.postIds.push(...postIds);
        this.current.demo = social.isDemo;
      }
      const when = TimeZoneUtil.format(check.date, settings.timezone);
      this.notifier.log('success', 'schedule', `${social.isDemo ? '[Demo] ' : ''}Scheduled "${item.name}" for ${when} on ${pluralize(postIds.length, 'channel')}.`);
      for (const failure of failures) {
        this.notifier.log('warn', 'schedule', `"${item.name}" was not scheduled on ${failure.name}: ${failure.message}`);
      }
      return 'done';
    } catch (error) {
      if (isCancelled(error)) {
        this.store.update(item.id, { busy: null, ...previous }, { reason: 'status', persist: false });
        throw error;
      }
      const message = error?.message || String(error);
      this.store.update(item.id, { busy: null, error: message, errorHint: error?.hint || '', errorPhase: 'schedule' }, { reason: 'status', persist: false });
      this.notifier.log('error', 'schedule', `Scheduling failed for "${item.name}": ${message}`, error?.hint || error?.details || '');
      throw error;
    }
  }

  /**
   * Queues the posts. Every request carries a key, and the server answers a key it has seen with the posts
   * it already queued for it. So when a failure leaves it unclear whether the server queued the post (a
   * timeout, a lost connection or a server error after the request went out), the same request is sent once
   * more: it queues the post or finds it, but never queues it twice.
   * @param {import('./services/social.js').SocialService} social
   * @param {{text: string, channelIds: string[], dueAt: Date, mediaUrl: string|null, thumbnailUrl?: string, requestKey?: string}} post
   * @param {{settings?: object, onRetry?: Function}} ctx
   */
  async createPosts(social, post, ctx) {
    const keyed = { ...post, requestKey: post.requestKey || uid('req') };
    try {
      return await social.schedulePost(keyed, ctx);
    } catch (error) {
      if (!SchedulerService.isUncertain(error)) throw error;
      this.notifier.log('warn', 'schedule', `The answer from the server was lost (${error.message}). Sending the same request again, which cannot queue the post twice.`);
      return social.schedulePost(keyed, ctx);
    }
  }

  /** @param {*} error @returns {boolean} whether the request failed in a way that may still have queued posts */
  static isUncertain(error) {
    return error instanceof ApiError && (error.uncertain || (!error.fromServer && ['timeout', 'network', 'server'].includes(error.kind)));
  }

  /**
   * The post text: chosen caption followed by the hashtags.
   * @param {object} item @returns {string}
   */
  composeText(item) {
    const content = item.content || emptyContent();
    const caption = String(item.useAlternative ? content.alternative_description : content.description).trim();
    const tags = (content.hashtags || []).join(' ');
    return [caption, tags].filter(Boolean).join('\n\n');
  }

  /**
   * The posts the planner would give a time to: not scheduled, not busy, and without a time unless `overwrite`.
   * @param {object[]} items @param {boolean} overwrite @returns {object[]}
   */
  plannable(items, overwrite) {
    const { mode } = this.getSettings();
    return items.filter((item) => !countedResult(item, mode) && !item.busy && (overwrite || !item.scheduleLocal));
  }

  /**
   * Publish times (in whole minutes) already taken by posts the planner leaves alone, so new times fit around them.
   * @param {object[]} targets the posts being planned @returns {Set<number>}
   */
  takenTimes(targets) {
    const settings = this.getSettings();
    const taken = new Set();
    for (const item of this.store.all()) {
      if (targets.includes(item)) continue;
      const result = countedResult(item, settings.mode);
      const due = result?.dueAt ? new Date(result.dueAt) : item.scheduleLocal ? TimeZoneUtil.zonedToUtc(item.scheduleLocal, settings.timezone)?.date : null;
      if (due) taken.add(Math.floor(due.getTime() / 60000));
    }
    return taken;
  }

  /**
   * The times the planner gives `count` posts: start, start + interval, … skipping minutes another post has.
   * @param {number} count @param {Date} start @param {number} intervalMinutes @param {Set<number>} taken @returns {Date[]}
   */
  planSlots(count, start, intervalMinutes, taken) {
    const slots = [];
    for (let k = 0; slots.length < count && k <= count + taken.size; k += 1) {
      const when = new Date(start.getTime() + k * intervalMinutes * 60000);
      if (!taken.has(Math.floor(when.getTime() / 60000))) slots.push(when);
    }
    return slots;
  }

  /**
   * Assigns publish times in gallery order: start, start + interval, …
   * Already-scheduled posts are never changed. A scheduling error left on a post is cleared, since its time changed.
   * @param {object[]} items @param {string} startLocal @param {number} intervalMinutes @param {boolean} overwrite
   * @returns {number} number of items updated
   */
  planTimes(items, startLocal, intervalMinutes, overwrite) {
    const settings = this.getSettings();
    const start = TimeZoneUtil.zonedToUtc(startLocal, settings.timezone);
    if (!start) throw new Error('Choose a valid start date and time.');
    const targets = this.plannable(items, overwrite);
    const slots = this.planSlots(targets.length, start.date, intervalMinutes, this.takenTimes(targets));
    targets.forEach((item, slot) => {
      const when = slots[slot];
      if (!when) return;
      const clears = {
        ...(item.errorPhase === 'schedule' ? { error: null, errorHint: '', errorPhase: null } : {}),
        // A post deleted from the queue comes back into the batch once it has a new time.
        ...(item.result?.removed ? { result: null } : {})
      };
      this.store.update(item.id, { scheduleLocal: TimeZoneUtil.toLocalInput(when, settings.timezone), showErrors: false, ...clears }, { reason: 'planned' });
    });
    return targets.length;
  }

  #emit(type, op) {
    this.dispatchEvent(new CustomEvent(type, { detail: op }));
  }
}
