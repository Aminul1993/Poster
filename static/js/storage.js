/** StorageService: localStorage that never crashes the app. */

/**
 * Wraps localStorage with JSON (de)serialisation, an in-memory mirror (so the
 * session keeps working when storage is blocked or full) and quota handling.
 * Emits "storage-error" and "storage-warning" events.
 */
export class StorageService extends EventTarget {
  /** @param {string} [namespace] */
  constructor(namespace = 'poster') {
    super();
    this.namespace = namespace;
    /** @type {Map<string, string>} latest serialised value per key */
    this.memory = new Map();
    this.persistent = StorageService.probe();
  }

  /** @returns {boolean} whether localStorage can be written */
  static probe() {
    try {
      const key = '__poster_probe__';
      window.localStorage.setItem(key, key);
      window.localStorage.removeItem(key);
      return true;
    } catch {
      return false;
    }
  }

  /** @param {*} error @returns {boolean} */
  static isQuotaError(error) {
    return error instanceof DOMException && (
      error.name === 'QuotaExceededError' || error.name === 'NS_ERROR_DOM_QUOTA_REACHED' ||
      error.code === 22 || error.code === 1014);
  }

  /** @param {string} name @returns {string} */
  key(name) { return `${this.namespace}.${name}`; }

  /**
   * Reads and parses a value.
   * @param {string} name @param {*} [fallback] @returns {*}
   */
  get(name, fallback = null) {
    const key = this.key(name);
    let raw = this.memory.has(key) ? this.memory.get(key) : null;
    if (raw === null && this.persistent) {
      try {
        raw = window.localStorage.getItem(key);
        if (raw !== null) this.memory.set(key, raw);
      } catch (error) {
        this.#emit('storage-error', { op: 'read', name, error });
      }
    }
    if (raw === null || raw === undefined) return fallback;
    try {
      return JSON.parse(raw);
    } catch (error) {
      this.#emit('storage-error', { op: 'parse', name, error });
      return fallback;
    }
  }

  /**
   * Serialises and stores a value. When the quota is exceeded and `shrink` is
   * given, the smaller value it returns is stored instead.
   * @param {string} name
   * @param {*} value
   * @param {{shrink?: (value: any) => any}} [options]
   * @returns {boolean} true when persisted to localStorage
   */
  set(name, value, { shrink } = {}) {
    let serialized;
    try {
      serialized = JSON.stringify(value);
    } catch (error) {
      this.#emit('storage-error', { op: 'serialize', name, error });
      return false;
    }
    const key = this.key(name);
    this.memory.set(key, serialized);
    if (!this.persistent) return false;
    try {
      window.localStorage.setItem(key, serialized);
      return true;
    } catch (error) {
      if (StorageService.isQuotaError(error) && typeof shrink === 'function') {
        try {
          const smaller = JSON.stringify(shrink(value));
          this.memory.set(key, smaller);
          window.localStorage.setItem(key, smaller);
          this.#emit('storage-warning', { name, message: `Browser storage is almost full, so older ${name} entries were trimmed.` });
          return true;
        } catch (retryError) {
          this.#emit('storage-error', { op: 'quota', name, error: retryError });
          return false;
        }
      }
      this.#emit('storage-error', { op: StorageService.isQuotaError(error) ? 'quota' : 'write', name, error });
      return false;
    }
  }

  /** @param {string} name */
  remove(name) {
    const key = this.key(name);
    this.memory.delete(key);
    if (!this.persistent) return;
    try { window.localStorage.removeItem(key); } catch (error) { this.#emit('storage-error', { op: 'write', name, error }); }
  }

  /** @returns {number} approximate bytes used by this app */
  usageBytes() {
    let total = 0;
    if (this.persistent) {
      try {
        for (let i = 0; i < window.localStorage.length; i += 1) {
          const key = window.localStorage.key(i);
          if (key && key.startsWith(`${this.namespace}.`)) total += (key.length + (window.localStorage.getItem(key) || '').length) * 2;
        }
        return total;
      } catch { total = 0; }
    }
    for (const [key, value] of this.memory) total += (key.length + value.length) * 2;
    return total;
  }

  #emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}
