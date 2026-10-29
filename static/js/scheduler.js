/** SchedulerService: bulk operations, progress and cancellation. */
import { LIMITS, STORAGE_KEYS } from './config.js';
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
 * @property {string[]} postIds Buffer post IDs a scheduling run created
 * @property {boolean} demo whether a scheduling run went to Demo mode's simulated Buffer
 * @property {string} [state] done | partial | failed | cancelled
 */
export class SchedulerService extends EventTarget {
  constructor({ storage, notifier, validation, store, getSettings, getOllama, getBuffer }) {
    super();
    this.storage = storage;
    this.notifier = notifier;
    this.validation = validation;
    this.store = store;
    this.getSettings = getSettings;
    this.getOllama = getOllama;
    this.getBuffer = getBuffer;
    /** @type {Operation|null} */
    this.current = null;
    // Scheduled posts are read from Buffer now; drop the list older versions kept in the browser.
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
    const ollama = this.getOllama();
    const previous = { error: item.error, errorHint: item.errorHint, errorPhase: item.errorPhase };
    this.store.update(item.id, { busy: 'generating', error: null, errorHint: '', errorPhase: null }, { reason: 'status', persist: false });
    try {
      const result = await ollama.generateContent(item, { signal, onRetry, settings });
      const replaced = hasContent(item) ? item.content : item.previousContent;
      this.store.update(item.id, {
        busy: null, content: result.content, previousContent: replaced, result: null, generatedAt: new Date().toISOString(), showErrors: false
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
   * cancels, so Poster and Buffer never disagree about what was scheduled.
   * @param {object} item
   * @param {{signal: AbortSignal, onRetry: Function}} ctx
   * @param {{skipScheduled?: boolean}} [options]
   * @returns {Promise<string>} "done" or "skipped"
   */
  async scheduleItem(item, { signal, onRetry }, { skipScheduled = false } = {}) {
    throwIfAborted(signal);
    const settings = this.getSettings();
    const buffer = this.getBuffer();
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
    const channelIds = buffer.selectedChannelIds(settings);
    if (!channelIds.length) {
      this.store.update(item.id, { busy: null }, { reason: 'status', persist: false });
      throw new ApiError('No Buffer channels selected. Choose at least one in Settings → Buffer.', { kind: 'config', service: 'Buffer' });
    }

    const previous = { error: item.error, errorHint: item.errorHint, errorPhase: item.errorPhase };
    this.store.update(item.id, { busy: 'scheduling', error: null, errorHint: '', errorPhase: null }, { reason: 'status', persist: false });
    try {
      const { url: mediaUrl, thumbnailUrl } = await buffer.uploadMedia(item, { settings, signal, onRetry });
      throwIfAborted(signal);
      if (mediaUrl && !item.mediaUrl && mediaUrl !== item.uploadedUrl) {
        // Simulated Demo URLs are never remembered, so Live mode cannot reuse them.
        if (!buffer.isDemo) this.store.update(item.id, { uploadedUrl: mediaUrl, uploadedThumbUrl: thumbnailUrl || '' }, { reason: 'status' });
      } else if (!mediaUrl) {
        this.notifier.log('warn', 'schedule', `"${item.name}" will be posted as text only (no public image URL).`);
      }

      const text = this.composeText(item);
      // Demo mode shows the thumbnail in its simulated posts list; the server ignores it.
      const listThumbnail = thumbnailUrl || item.uploadedThumbUrl || item.thumbDataUrl;
      const result = await this.createPosts(buffer, { text, channelIds, dueAt: check.date, mediaUrl, thumbnailUrl: listThumbnail }, { settings, onRetry });
      const postIds = result.posts.map((p) => p.postId);
      const failures = result.failures.map((f) => ({ id: f.channelId, name: buffer.channelName(f.channelId, settings), message: f.message }));
      this.store.update(item.id, {
        busy: null, error: null, errorHint: '', errorPhase: null, showErrors: false,
        result: {
          dueAt: check.date.toISOString(), timezone: settings.timezone, mode: buffer.isDemo ? 'demo' : 'live', postIds,
          channels: result.posts.map((p) => buffer.channelName(p.channelId, settings)), partial: failures.length > 0, failures
        }
      }, { reason: 'scheduled' });
      if (this.current) {
        this.current.dueTimes.push(check.date.getTime());
        this.current.postIds.push(...postIds);
        this.current.demo = buffer.isDemo;
      }
      const when = TimeZoneUtil.format(check.date, settings.timezone);
      this.notifier.log('success', 'schedule', `${buffer.isDemo ? '[Demo] ' : ''}Scheduled "${item.name}" for ${when} on ${pluralize(postIds.length, 'channel')}.`);
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
   * Creates the posts. When a failure leaves it unclear whether Buffer created a post (a timeout, a lost
   * connection or a server error after the request went out), Buffer's upcoming posts are checked for it,
   * so the image is not scheduled a second time.
   * @param {import('./services/buffer.js').BufferService} buffer
   * @param {{text: string, channelIds: string[], dueAt: Date, mediaUrl: string|null, thumbnailUrl?: string}} post
   * @param {{settings?: object, onRetry?: Function}} ctx
   */
  async createPosts(buffer, post, ctx) {
    let result;
    try {
      result = await buffer.schedulePost(post, ctx);
    } catch (error) {
      if (!SchedulerService.isUncertain(error)) throw error;
      const found = await this.findCreatedPosts(buffer, post, post.channelIds);
      if (!found.length) throw error;
      this.notifier.log('warn', 'schedule', `The answer from Buffer was lost (${error.message}), but the post is in Buffer, so it counts as scheduled.`);
      const missing = post.channelIds.filter((id) => !found.some((p) => p.channelId === id));
      return { posts: found, failures: missing.map((channelId) => ({ channelId, message: error.message, uncertain: true })) };
    }
    const unsure = result.failures.filter((f) => f.uncertain);
    if (!unsure.length) return result;
    const found = await this.findCreatedPosts(buffer, post, unsure.map((f) => f.channelId));
    if (!found.length) return result;
    this.notifier.log('warn', 'schedule', `The answer from Buffer was lost for ${pluralize(found.length, 'channel')}, but the post is in Buffer, so it counts as scheduled.`);
    return { posts: [...result.posts, ...found], failures: result.failures.filter((f) => !found.some((p) => p.channelId === f.channelId)) };
  }

  /** @param {*} error @returns {boolean} whether the request failed in a way that may still have created posts */
  static isUncertain(error) {
    return error instanceof ApiError && (error.uncertain || (!error.fromServer && ['timeout', 'network', 'server'].includes(error.kind)));
  }

  /**
   * Upcoming Buffer posts on these channels with this exact text and publish time (to the minute), one per channel.
   * Upcoming posts come soonest first, so pages are read only until they pass the publish time.
   * @returns {Promise<Array<{channelId: string, postId: string, dueAt: string, status: string}>>}
   */
  async findCreatedPosts(buffer, post, channelIds) {
    const due = post.dueAt.getTime();
    const matches = (p) => channelIds.includes(p.channelId) && p.text === post.text && p.dueAt && Math.abs(Date.parse(p.dueAt) - due) < 60000;
    const found = [];
    try {
      let after = null;
      for (let page = 0; page < LIMITS.reconcilePages; page += 1) {
        const { posts, next } = await buffer.listPosts({ filter: 'upcoming', after });
        found.push(...posts.filter(matches));
        const last = posts[posts.length - 1];
        if (!next || !last?.dueAt || Date.parse(last.dueAt) > due + 60000) break;
        after = next;
      }
    } catch {
      // Keep what was found; without it the error stands and the user decides.
    }
    return found
      .filter((p, index, all) => all.findIndex((q) => q.channelId === p.channelId) === index)
      .map((p) => ({ channelId: p.channelId, postId: p.postId, dueAt: p.dueAt, status: p.status }));
  }

  /**
   * The text sent to Buffer: chosen caption followed by the hashtags.
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
    targets.forEach((item, slot) => {
      const when = new Date(start.date.getTime() + slot * intervalMinutes * 60000);
      const clears = {
        ...(item.errorPhase === 'schedule' ? { error: null, errorHint: '', errorPhase: null } : {}),
        // A post deleted from Buffer comes back into the batch once it has a new time.
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
