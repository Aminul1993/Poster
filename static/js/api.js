/** ApiService and retryRequest(): fetch to the Poster server with timeouts, cancellation and errors. */
import { API_CONFIG } from './config.js';
import { ApiError, cancelledError, isCancelled, sleep, throwIfAborted } from './errors.js';
import { formatDuration, isPlainObject, truncate } from './util.js';

/**
 * Default retry predicate: timeouts, temporary network failures and HTTP
 * 429/500/502/503/504. Never 400/401/403, validation errors or errors the
 * Poster server already retried.
 * @param {*} error @returns {boolean}
 */
export function isRetryableError(error) {
  return error instanceof ApiError && error.retryable;
}

/**
 * Runs `operation` and retries it on transient failures with backoff
 * (1 s, 2 s, 4 s; at most 3 retries by default).
 * @template T
 * @param {(attempt: number) => Promise<T>} operation receives the attempt number (0 = first try)
 * @param {{maxRetries?: number, delaysMs?: number[], shouldRetry?: (error: *) => boolean,
 *          onRetry?: (info: {attempt: number, maxRetries: number, delayMs: number, error: *}) => void,
 *          signal?: AbortSignal}} [options]
 * @returns {Promise<T>}
 */
export async function retryRequest(operation, options = {}) {
  const {
    maxRetries = API_CONFIG.retry.maxRetries,
    delaysMs = API_CONFIG.retry.delaysMs,
    shouldRetry = isRetryableError,
    onRetry = null,
    signal = null
  } = options;
  let attempt = 0;
  for (;;) {
    throwIfAborted(signal);
    try {
      return await operation(attempt);
    } catch (error) {
      if (isCancelled(error) || signal?.aborted || attempt >= maxRetries || !shouldRetry(error)) throw error;
      const delayMs = delaysMs[Math.min(attempt, delaysMs.length - 1)];
      attempt += 1;
      if (typeof onRetry === 'function') {
        try { onRetry({ attempt, maxRetries, delayMs, error }); } catch { /* a logging failure must not break the retry */ }
      }
      await sleep(delayMs, signal);
    }
  }
}

/**
 * Replays the retries the server performed into the caller's onRetry
 * callback, so they show up in the activity log and operation statistics.
 * @param {Array<object>} retries @param {Function} [onRetry]
 */
export function relayRetries(retries, onRetry) {
  if (typeof onRetry !== 'function' || !Array.isArray(retries)) return;
  for (const retry of retries) {
    try {
      onRetry({
        attempt: Number(retry.attempt) || 0,
        maxRetries: Number(retry.maxRetries) || API_CONFIG.retry.maxRetries,
        delayMs: Number(retry.delayMs) || 0,
        error: { message: String(retry.message || '') },
        server: true
      });
    } catch { /* a logging failure must not break the request */ }
  }
}

/**
 * fetch() wrapper for the Poster server's /api routes: JSON or multipart in,
 * JSON out, timeouts, cancellation, the CSRF header, and conversion of the
 * server's classified errors into ApiError. All requests are same-origin.
 */
export class ApiService {
  /**
   * POSTs to a server route and relays the server's retries to `onRetry`.
   * @param {string} path a route from API_CONFIG.routes
   * @param {{json?: object, form?: FormData, timeoutMs?: number, signal?: AbortSignal, service?: string, onRetry?: Function}} [options]
   * @returns {Promise<object>} parsed JSON response
   * @throws {ApiError}
   */
  async call(path, { onRetry, ...options } = {}) {
    try {
      const data = await this.post(path, options);
      relayRetries(data.retries, onRetry);
      return data;
    } catch (error) {
      if (error instanceof ApiError) relayRetries(error.retries, onRetry);
      throw error;
    }
  }

  /**
   * call(), retried when the Poster server could not be reached (see ApiError#retryable).
   * @param {string} path a route from API_CONFIG.routes
   * @param {{json?: object, form?: FormData, timeoutMs?: number, signal?: AbortSignal, service?: string, onRetry?: Function}} [options]
   * @returns {Promise<object>}
   */
  callWithRetry(path, options = {}) {
    return retryRequest(() => this.call(path, options), { signal: options.signal, onRetry: options.onRetry });
  }

  /**
   * @param {string} path
   * @param {{json?: object, form?: FormData, timeoutMs?: number, signal?: AbortSignal, service?: string}} [options]
   * @returns {Promise<object>}
   * @throws {ApiError}
   */
  async post(path, { json, form, timeoutMs = 60000, signal, service = 'Poster server' } = {}) {
    throwIfAborted(signal);
    if (navigator.onLine === false) {
      throw new ApiError('You are offline. Reconnect to the internet and try again.', { kind: 'offline', service });
    }
    const headers = { Accept: 'application/json', 'X-Poster-Request': '1' };
    let body = form;
    if (json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(json);
    }

    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const forwardAbort = () => controller.abort();
    signal?.addEventListener('abort', forwardAbort, { once: true });

    try {
      let response;
      try {
        response = await fetch(path, {
          method: 'POST', headers, body, signal: controller.signal, cache: 'no-store', credentials: 'same-origin'
        });
      } catch (error) {
        if (timedOut) throw this.#timeoutError(service, timeoutMs);
        if (signal?.aborted) throw cancelledError();
        throw new ApiError('Cannot reach the Poster server. Make sure "python app.py" is still running.', {
          kind: 'network', service: 'Poster server', hint: 'Start or restart the server, then try again.', cause: error
        });
      }

      let text = '';
      try {
        text = await response.text();
      } catch (error) {
        if (timedOut) throw this.#timeoutError(service, timeoutMs);
        if (signal?.aborted) throw cancelledError();
        throw new ApiError('The connection to the Poster server was interrupted while reading the response.', { kind: 'network', service: 'Poster server', cause: error });
      }
      let data = null;
      if (text) {
        try { data = JSON.parse(text); } catch { data = null; }
      }
      if (!response.ok) throw ApiService.errorFromResponse(response.status, data, service);
      if (!isPlainObject(data)) {
        throw new ApiError('The Poster server returned an unexpected response.', { kind: 'parse', service: 'Poster server', details: truncate(text, 300) });
      }
      return data;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', forwardAbort);
    }
  }

  #timeoutError(service, timeoutMs) {
    return new ApiError(`${service} did not finish within ${formatDuration(timeoutMs)}.`, {
      kind: 'timeout', service, hint: 'The server may be busy. Try again in a moment.'
    });
  }

  /**
   * Rebuilds the server's classified error, or classifies a bare HTTP failure
   * (for example from a reverse proxy in front of the server).
   * @param {number} status @param {*} data @param {string} service
   * @returns {ApiError}
   */
  static errorFromResponse(status, data, service) {
    const error = isPlainObject(data) && isPlainObject(data.error) ? data.error : null;
    if (error && typeof error.message === 'string') {
      return new ApiError(error.message, {
        kind: typeof error.kind === 'string' ? error.kind : 'server',
        status: Number(error.status) || 0,
        service: typeof error.service === 'string' && error.service ? error.service : service,
        hint: typeof error.hint === 'string' ? error.hint : '',
        details: typeof error.details === 'string' ? error.details : '',
        retries: Array.isArray(error.retries) ? error.retries : [],
        uncertain: error.uncertain === true,
        fromServer: true
      });
    }
    // A gateway timeout (Vercel stops a function at its time limit) means the server already spent its whole
    // budget on this request, retries included: trying again would only repeat that.
    if (status === 504) return new ApiError('The Poster server ran out of time (HTTP 504).', { kind: 'timeout', status, service, fromServer: true, uncertain: true, hint: 'The service is slow right now. Try again in a minute.' });
    if (status === 413) return new ApiError('The upload is larger than the Poster server accepts.', { kind: 'validation', status, service });
    if (status === 404 || status === 405) return new ApiError('The Poster server does not provide this API. Make sure app.py is up to date.', { kind: 'config', status, service });
    if (status >= 500) return new ApiError(`The Poster server failed (HTTP ${status}).`, { kind: 'server', status, service, hint: 'Check the server log.' });
    return new ApiError(`The Poster server rejected the request (HTTP ${status}).`, { kind: 'client', status, service });
  }
}
