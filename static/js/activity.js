/** ActivityLog: the Activity log card's entries, kept in the server's database. */
import { API_CONFIG, LIMITS, RULES, SERVER, STORAGE_KEYS, TIMING } from './config.js';
import { debounce, isPlainObject, truncate, uid } from './util.js';

/**
 * @typedef {Object} LogEntry
 * @property {string} id
 * @property {string} ts ISO timestamp
 * @property {'info'|'success'|'warn'|'error'} level
 * @property {string} category user | ai | image | schedule | retry | upload | settings | system | publish (the server's publisher)
 * @property {string} message
 * @property {string} [details]
 */

const LEVELS = ['info', 'success', 'warn', 'error'];

/** The log's filters, as the server applies them: entries at any of `levels` or in any of `categories` (null: every entry). */
export const ACTIVITY_FILTERS = RULES.activity.filters;

/** @param {LogEntry} entry @param {string} filter @returns {boolean} whether the filter shows the entry */
export function matchesFilter(entry, filter) {
  const rule = ACTIVITY_FILTERS[filter];
  if (!rule) return true;
  return (rule.levels || []).includes(entry.level) || (rule.categories || []).includes(entry.category);
}

/** @param {*} e @returns {LogEntry|null} */
export function normalizeEntry(e) {
  if (!isPlainObject(e) || typeof e.id !== 'string' || typeof e.ts !== 'string' || typeof e.message !== 'string') return null;
  const entry = { id: e.id, ts: e.ts, level: LEVELS.includes(e.level) ? e.level : 'info', category: String(e.category || 'system'), message: e.message };
  if (typeof e.details === 'string' && e.details) entry.details = e.details;
  return entry;
}

/** @param {LogEntry[]} entries @returns {LogEntry[]} newest first, in the server's order */
function newestFirst(entries) {
  const key = (e) => `${e.ts}|${e.id}`;
  return entries.sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0));
}

/**
 * Entries are recorded here as they happen and saved to the server's
 * database in batches; the card shows the newest page from the database, plus
 * entries not saved yet. Those also wait in browser storage, so a reload or a
 * stopped server does not lose them (and a log kept by an earlier version is
 * saved on the first start). Without a database (on Vercel before Postgres is
 * added) the log stays in browser storage. Emits "change" whenever there is
 * something new to render.
 */
export class ActivityLog extends EventTarget {
  /** @param {{api: ApiService, storage: StorageService}} deps */
  constructor({ api, storage }) {
    super();
    this.api = api;
    this.storage = storage;
    /** Whether the server keeps the log in its database. */
    this.remote = Boolean(SERVER.social.database?.configured);
    /** @type {string} one of ACTIVITY_FILTERS */
    this.filter = 'all';
    /** @type {LogEntry[]} entries read from the database for the filter, newest first */
    this.saved = [];
    const stored = storage.get(STORAGE_KEYS.log, []);
    /** @type {LogEntry[]} entries not in the database yet (every entry without one), oldest first */
    this.unsaved = Array.isArray(stored) ? stored.map(normalizeEntry).filter(Boolean).slice(-LIMITS.maxLogEntries) : [];
    /** @type {string|null} cursor for the next page */
    this.next = null;
    /** @type {false|'all'|'more'} */
    this.loading = false;
    /** @type {ApiError|null} why the last load failed */
    this.error = null;
    /** @type {number|null} when the log was last read from the server */
    this.loadedAt = null;
    this.request = 0;
    this.lastTs = 0;
    this.saving = false;
    this.retryTimer = null;
    /** Counts clear()s, so a save that was in flight does not bring back what was cleared. */
    this.clears = 0;
    this.persist = debounce(() => {
      this.storage.set(STORAGE_KEYS.log, this.unsaved, { shrink: (entries) => entries.slice(-LIMITS.logEntriesWhenFull) });
    }, TIMING.logPersistMs);
    this.save = debounce(() => this.#save(), TIMING.logPersistMs);
  }

  /** @returns {LogEntry[]} what the filter shows, newest first */
  get entries() {
    const known = new Set(this.saved.map((e) => e.id));
    return newestFirst([...this.unsaved.filter((e) => !known.has(e.id) && matchesFilter(e, this.filter)), ...this.saved]);
  }

  /**
   * Records an entry and saves it to the server soon after.
   * @param {LogEntry['level']} level @param {string} category @param {string} message @param {string} [details]
   * @returns {LogEntry}
   */
  add(level, category, message, details = '') {
    // Distinct times keep entries recorded together in the order they happened.
    this.lastTs = Math.max(Date.now(), this.lastTs + 1);
    const entry = { id: uid('log'), ts: new Date(this.lastTs).toISOString(), level, category, message: String(message) };
    if (details) entry.details = truncate(String(details), 600);
    this.unsaved.push(entry);
    if (this.unsaved.length > LIMITS.maxLogEntries) this.unsaved.splice(0, this.unsaved.length - LIMITS.maxLogEntries);
    this.persist();
    if (this.remote) this.save();
    this.#emit();
    return entry;
  }

  /** @param {string} filter one of ACTIVITY_FILTERS */
  setFilter(filter) {
    if (!(filter in ACTIVITY_FILTERS) || filter === this.filter) return;
    this.filter = filter;
    this.saved = [];
    this.next = null;
    if (this.remote) this.load(); else this.#emit();
  }

  /**
   * Reads the newest page (or, with `more`, the next one) from the server, and
   * saves entries still waiting from an earlier visit.
   * @param {{more?: boolean}} [options]
   */
  async load({ more = false } = {}) {
    if (!this.remote) return;
    if (more && (!this.next || this.loading)) return;
    if (!more && this.unsaved.length) this.save();
    const request = ++this.request;
    this.loading = more ? 'more' : 'all';
    this.error = null;
    this.#emit();
    try {
      const data = await this.api.call(API_CONFIG.routes.activityList, {
        json: { filter: this.filter, after: more ? this.next : null }, timeoutMs: API_CONFIG.timeouts.activityMs, service: 'Poster server'
      });
      if (request !== this.request) return;
      const page = (Array.isArray(data.entries) ? data.entries : []).map(normalizeEntry).filter(Boolean);
      const known = new Set(more ? this.saved.map((e) => e.id) : []);
      this.saved = newestFirst([...(more ? this.saved : []), ...page.filter((e) => !known.has(e.id))]);
      this.next = typeof data.next === 'string' && data.next ? data.next : null;
      this.loadedAt = Date.now();
    } catch (error) {
      if (request !== this.request) return;
      this.error = error;
    } finally {
      if (request === this.request) {
        this.loading = false;
        this.#emit();
      }
    }
  }

  /** Deletes every entry, on the server and in this browser. Throws when the server did not delete them. */
  async clear() {
    if (this.remote) await this.api.call(API_CONFIG.routes.activityClear, { json: {}, timeoutMs: API_CONFIG.timeouts.activityMs, service: 'Poster server' });
    this.clears += 1;
    this.request += 1;  // a page still loading would bring the entries back
    this.loading = false;
    this.saved = [];
    this.unsaved = [];
    this.next = null;
    this.persist();
    this.persist.flush();
    this.#emit();
  }

  /** Sends waiting entries to the server, a batch at a time; when it cannot be reached, tries again later. */
  async #save() {
    if (this.saving || !this.remote || !this.unsaved.length) return;
    clearTimeout(this.retryTimer);
    this.saving = true;
    const batch = this.unsaved.slice(0, RULES.activity.maxBatch);
    const clears = this.clears;
    try {
      await this.api.call(API_CONFIG.routes.activitySave, { json: { entries: batch }, timeoutMs: API_CONFIG.timeouts.activityMs, service: 'Poster server' });
    } catch (error) {
      if (error.kind === 'config') {
        // The server has no database after all (or predates the log): keep the log in this browser.
        this.remote = false;
        this.#emit();
        return;
      }
      // A batch the server refuses would be refused every time, so only a failure to reach it is tried again.
      if (error.kind !== 'validation') {
        this.retryTimer = setTimeout(() => this.save(), TIMING.logRetryMs);
        return;
      }
    } finally {
      this.saving = false;
    }
    if (clears !== this.clears) return;
    const ids = new Set(batch.map((e) => e.id));
    this.unsaved = this.unsaved.filter((e) => !ids.has(e.id));
    const known = new Set(this.saved.map((e) => e.id));
    this.saved = newestFirst([...batch.filter((e) => !known.has(e.id) && matchesFilter(e, this.filter)), ...this.saved]);
    this.persist();
    this.#emit();
    if (this.unsaved.length) this.save();
  }

  #emit() {
    this.dispatchEvent(new CustomEvent('change'));
  }
}
