/** ApiError, and cancellation helpers built on it. */
import { API_CONFIG } from './config.js';

/** Error raised by every API call, classified for messaging and retries. */
export class ApiError extends Error {
  /**
   * @param {string} message user-facing message
   * @param {{kind?: string, status?: number, service?: string, details?: string, hint?: string,
   *          fromServer?: boolean, uncertain?: boolean, retries?: Array<object>, cause?: *}} [options]
   *   kind: cancelled | timeout | network | offline | auth | rate_limit | server | client | not_found | parse | config | validation
   *   fromServer: the Poster server classified this error (after its own retries)
   *   uncertain: the request failed, but may still have taken effect (a post may exist in Buffer)
   *   retries: retries the server performed before giving up
   */
  constructor(message, { kind = 'unknown', status = 0, service = '', details = '', hint = '', fromServer = false, uncertain = false, retries = [], cause } = {}) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.service = service;
    this.details = details;
    this.hint = hint;
    this.fromServer = fromServer;
    this.uncertain = uncertain;
    this.retries = retries;
    if (cause !== undefined) this.cause = cause;
  }

  /**
   * Whether retryRequest() should try again. Errors reported by the Poster
   * server are final, because the server already retried Ollama or Buffer.
   */
  get retryable() {
    if (this.fromServer) return false;
    return API_CONFIG.retry.retryableKinds.includes(this.kind) || API_CONFIG.retry.retryableStatus.includes(this.status);
  }

  /**
   * Errors that will affect every following item, so bulk runs stop early.
   * A network error is only seen here after retries are used up, which means
   * the host is unreachable rather than briefly flaky.
   */
  get fatal() {
    return ['auth', 'offline', 'config', 'network'].includes(this.kind);
  }
}

/**
 * Sleeps for `ms`, rejecting early with a "cancelled" ApiError when aborted.
 * @param {number} ms
 * @param {AbortSignal} [signal]
 * @returns {Promise<void>}
 */
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(cancelledError()); return; }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, ms);
    function onAbort() { clearTimeout(timer); reject(cancelledError()); }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** @param {AbortSignal} [signal] */
export function throwIfAborted(signal) {
  if (signal?.aborted) throw cancelledError();
}

/** @returns {ApiError} */
export function cancelledError() {
  return new ApiError('Operation cancelled.', { kind: 'cancelled' });
}

/** @param {*} error @returns {boolean} */
export function isCancelled(error) {
  return error instanceof ApiError && error.kind === 'cancelled';
}
