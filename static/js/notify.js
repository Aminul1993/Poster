/** NotificationService: toasts, banners and the activity log. */
import { LIMITS, STORAGE_KEYS, TIMING } from './config.js';
import { $, debounce, el, icon, isPlainObject, truncate, uid } from './util.js';

/**
 * @typedef {Object} LogEntry
 * @property {string} id
 * @property {string} ts ISO timestamp
 * @property {'info'|'success'|'warn'|'error'} level
 * @property {string} category user | ai | schedule | retry | upload | settings | system
 * @property {string} message
 * @property {string} [details]
 */
export class NotificationService extends EventTarget {
  /** @param {StorageService} storage */
  constructor(storage) {
    super();
    this.storage = storage;
    this.toastRegion = $('#toast-region');
    this.bannerRegion = $('#banners');
    const stored = storage.get(STORAGE_KEYS.log, []);
    /** @type {LogEntry[]} */
    this.entries = Array.isArray(stored) ? stored.filter((e) => isPlainObject(e) && typeof e.message === 'string').slice(-LIMITS.maxLogEntries) : [];
    this.persistLog = debounce(() => {
      this.storage.set(STORAGE_KEYS.log, this.entries, { shrink: (entries) => entries.slice(-LIMITS.logEntriesWhenFull) });
    }, TIMING.logPersistMs);
  }

  /**
   * Shows a toast notification.
   * @param {string} message
   * @param {'info'|'success'|'warning'|'error'} [type]
   * @param {{title?: string, timeout?: number}} [options] timeout 0 keeps it open
   * @returns {HTMLElement}
   */
  toast(message, type = 'info', { title, timeout } = {}) {
    const icons = { success: 'check-circle', error: 'alert-circle', warning: 'alert', info: 'info' };
    const kind = icons[type] ? type : 'info';
    const duration = timeout ?? TIMING.toastMs[kind];
    const closeButton = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs toast-close', 'aria-label': 'Dismiss notification' }, [icon('x')]);
    const toast = el('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : 'status' }, [
      icon(icons[kind]),
      el('div', {}, [
        title ? el('div', { class: 'toast-title', text: title }) : null,
        el('div', { class: title ? 'toast-msg' : 'toast-title', text: message })
      ]),
      closeButton,
      duration > 0 ? el('div', { class: 'toast-timer', style: `animation-duration:${duration}ms` }) : null
    ]);

    let remaining = duration;
    let startedAt = 0;
    let timer = null;
    const dismiss = () => {
      clearTimeout(timer);
      if (!toast.isConnected || toast.classList.contains('is-leaving')) return;
      toast.classList.add('is-leaving');
      setTimeout(() => toast.remove(), 200);
    };
    const start = () => {
      if (duration <= 0) return;
      startedAt = Date.now();
      timer = setTimeout(dismiss, remaining);
    };
    const pause = () => {
      if (duration <= 0 || !timer) return;
      clearTimeout(timer);
      timer = null;
      remaining = Math.max(800, remaining - (Date.now() - startedAt));
    };
    closeButton.addEventListener('click', dismiss);
    toast.addEventListener('mouseenter', pause);
    toast.addEventListener('mouseleave', () => { if (!timer) start(); });
    toast.addEventListener('focusin', pause);
    toast.addEventListener('focusout', () => { if (!timer) start(); });

    this.toastRegion.append(toast);
    // Keep the stack short so toasts never bury the page (tighter on phones).
    const maxToasts = window.matchMedia?.('(max-width: 560px)').matches ? 2 : 4;
    while (this.toastRegion.children.length > maxToasts) this.toastRegion.firstElementChild.remove();
    start();
    return toast;
  }

  /**
   * Shows (or replaces) a persistent banner above the dashboard.
   * @param {string} id unique banner id
   * @param {{type?: 'info'|'warning'|'error'|'success', title: string, message?: string,
   *          actions?: Array<{label: string, onClick: Function, primary?: boolean}>, dismissible?: boolean}} options
   */
  banner(id, { type = 'info', title, message = '', actions = [], dismissible = true }) {
    const icons = { info: 'info', warning: 'alert', error: 'alert-circle', success: 'check-circle' };
    const existing = this.bannerRegion.querySelector(`[data-banner-id="${CSS.escape(id)}"]`);
    const banner = el('div', { class: `banner banner-${type}`, role: type === 'error' ? 'alert' : 'status', dataset: { bannerId: id } }, [
      icon(icons[type] || 'info'),
      el('div', { class: 'banner-body' }, [
        el('div', { class: 'banner-title', text: title }),
        message ? el('div', { class: 'banner-msg', text: message }) : null
      ]),
      el('div', { class: 'banner-actions' }, [
        ...actions.map((action) => el('button', {
          type: 'button',
          class: `btn btn-sm${action.primary ? ' btn-primary' : ''}`,
          text: action.label,
          onclick: () => action.onClick()
        })),
        dismissible ? el('button', {
          type: 'button', class: 'btn btn-ghost btn-icon btn-sm', 'aria-label': `Dismiss: ${title}`,
          onclick: () => this.clearBanner(id)
        }, [icon('x')]) : null
      ])
    ]);
    if (existing) existing.replaceWith(banner); else this.bannerRegion.append(banner);
  }

  /** @param {string} id */
  clearBanner(id) {
    this.bannerRegion.querySelector(`[data-banner-id="${CSS.escape(id)}"]`)?.remove();
  }

  /**
   * Records an activity log entry (persisted, newest last).
   * @param {LogEntry['level']} level
   * @param {string} category
   * @param {string} message
   * @param {string} [details]
   * @returns {LogEntry}
   */
  log(level, category, message, details = '') {
    const entry = { id: uid('log'), ts: new Date().toISOString(), level, category, message: String(message) };
    if (details) entry.details = truncate(String(details), 600);
    this.entries.push(entry);
    if (this.entries.length > LIMITS.maxLogEntries) this.entries.splice(0, this.entries.length - LIMITS.maxLogEntries);
    this.persistLog();
    this.dispatchEvent(new CustomEvent('log', { detail: entry }));
    return entry;
  }

  /** Removes every log entry. */
  clearLog() {
    this.entries = [];
    this.persistLog();
    this.persistLog.flush();
    this.dispatchEvent(new CustomEvent('log', { detail: null }));
  }
}
