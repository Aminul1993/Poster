/** NotificationService: toasts, banners and entries for the activity log. */
import { TIMING } from './config.js';
import { $, el, icon } from './util.js';

export class NotificationService extends EventTarget {
  /** @param {import('./activity.js').ActivityLog} activity where log() records entries */
  constructor(activity) {
    super();
    this.activity = activity;
    this.toastRegion = $('#toast-region');
    this.bannerRegion = $('#banners');
  }

  /**
   * Shows a toast notification.
   * @param {string} message
   * @param {'info'|'success'|'warning'|'error'} [type]
   * @param {{title?: string, timeout?: number, action?: {label: string, onClick: Function}, onClose?: Function}} [options]
   *   timeout 0 keeps it open; action adds one button (Undo) that closes the toast; onClose runs once when it goes
   * @returns {HTMLElement}
   */
  toast(message, type = 'info', { title, timeout, action, onClose } = {}) {
    const icons = { success: 'check-circle', error: 'alert-circle', warning: 'alert', info: 'info' };
    const kind = icons[type] ? type : 'info';
    const duration = timeout ?? TIMING.toastMs[kind];
    const closeButton = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs toast-close', 'aria-label': 'Dismiss notification' }, [icon('x')]);
    const actionButton = action ? el('button', { type: 'button', class: 'btn btn-sm toast-action', text: action.label }) : null;
    const toast = el('div', { class: `toast toast-${kind}`, role: kind === 'error' ? 'alert' : 'status' }, [
      icon(icons[kind]),
      el('div', {}, [
        title ? el('div', { class: 'toast-title', text: title }) : null,
        el('div', { class: title ? 'toast-msg' : 'toast-title', text: message }),
        actionButton
      ]),
      closeButton,
      duration > 0 ? el('div', { class: 'toast-timer', style: `animation-duration:${duration}ms` }) : null
    ]);

    let remaining = duration;
    let startedAt = 0;
    let timer = null;
    let closed = false;
    // Runs once however the toast goes: timed out, dismissed, its action used, or pushed out by newer toasts.
    toast._close = () => {
      if (closed) return;
      closed = true;
      onClose?.();
    };
    const dismiss = () => {
      clearTimeout(timer);
      if (!toast.isConnected || toast.classList.contains('is-leaving')) return;
      toast.classList.add('is-leaving');
      toast._close();
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
    actionButton?.addEventListener('click', () => { action.onClick(); dismiss(); });
    toast.addEventListener('mouseenter', pause);
    toast.addEventListener('mouseleave', () => { if (!timer) start(); });
    toast.addEventListener('focusin', pause);
    toast.addEventListener('focusout', () => { if (!timer) start(); });

    this.toastRegion.append(toast);
    // Keep the stack short so toasts never bury the page (tighter on phones).
    // Two at most, so toasts never stack over the cards being reviewed.
    const maxToasts = 2;
    while (this.toastRegion.children.length > maxToasts) {
      const oldest = this.toastRegion.firstElementChild;
      oldest._close?.();
      oldest.remove();
    }
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
   * Records an activity log entry, which is saved to the server's database.
   * @param {import('./activity.js').LogEntry['level']} level
   * @param {string} category
   * @param {string} message
   * @param {string} [details]
   * @returns {import('./activity.js').LogEntry}
   */
  log(level, category, message, details = '') {
    return this.activity.add(level, category, message, details);
  }
}
