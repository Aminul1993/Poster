/** The Activity log card. */
import { showToast } from '../app.js';
import { TimeZoneUtil } from '../time.js';
import { el, pluralize } from '../util.js';
import { byId } from './dom.js';
import { View } from './view.js';

export class LogView extends View {
  constructor(ui) {
    super(ui);
    this.renderQueued = false;
  }

  get activity() { return this.app.activity; }

  bind() {
    byId('log-filter').addEventListener('change', (event) => {
      this.ui.prefs.logFilter = event.target.value;
      this.ui.saveUiPrefs();
      this.activity.setFilter(event.target.value);
    });
    byId('log-refresh').addEventListener('click', () => this.activity.load());
    byId('log-more').addEventListener('click', () => this.activity.load({ more: true }));
    byId('clear-log').addEventListener('click', async () => {
      const where = this.activity.remote ? 'from the server' : 'from this browser';
      const ok = await this.ui.confirm({ title: 'Clear the activity log?', message: `All log entries are deleted ${where}.`, confirmLabel: 'Clear log', danger: true });
      if (!ok) return;
      try {
        await this.activity.clear();
        showToast('Activity log cleared.', 'info');
      } catch (error) {
        showToast(error.message, 'error', { title: 'Could not clear the activity log' });
      }
    });
  }

  /** Batches log renders to one per frame. */
  queueRender() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => { this.renderQueued = false; this.render(); });
  }

  render() {
    const log = this.activity;
    const entries = log.entries;
    const list = byId('log-list');
    const today = new Date().toDateString();
    list.replaceChildren(...entries.map((entry) => {
      const date = new Date(entry.ts);
      // Device time, in the dashboard's one format: "4:00:05 pm" today, "Fri, 30 Oct, 4:00 pm" before.
      const time = date.toDateString() === today ? TimeZoneUtil.formatTime(date, undefined, { seconds: true }) : TimeZoneUtil.format(date, undefined);
      return el('li', { class: 'log-item', dataset: { level: entry.level } }, [
        el('time', { class: 'log-time', datetime: entry.ts, title: TimeZoneUtil.format(date, undefined), text: time }),
        el('span', { class: 'log-cat', text: entry.category }),
        el('span', { class: 'log-msg' }, [entry.message, entry.details ? el('span', { class: 'log-detail', text: entry.details }) : null])
      ]);
    }));
    byId('log-count').textContent = `${entries.length}${log.next ? '+' : ''}`;
    byId('clear-log').disabled = log.filter === 'all' && !entries.length && !log.next;
    byId('log-refresh').hidden = !log.remote;
    byId('log-refresh').disabled = Boolean(log.loading);
    byId('log-refresh').classList.toggle('is-loading', log.loading === 'all');
    list.setAttribute('aria-busy', String(Boolean(log.loading)));
    list.hidden = !entries.length;
    byId('log-empty').hidden = entries.length > 0;
    if (!entries.length) {
      const pending = log.remote && (log.loading || (!log.loadedAt && !log.error));
      byId('log-empty-title').textContent = pending ? 'Loading the activity log…'
        : log.error ? 'Could not load the activity log'
          : log.filter === 'all' ? 'No activity yet' : 'No matching activity';
      byId('log-empty-text').textContent = pending ? ''
        : log.error ? [log.error.message, log.error.hint].filter(Boolean).join(' ')
          : log.filter === 'all' ? 'Uploads, generations, schedules, publishing, retries and errors are recorded here with timestamps.'
            : 'Nothing in the log matches this filter.';
    }
    const waiting = log.remote ? log.unsaved.length : 0;
    byId('log-source').textContent = !log.remote
      ? 'Kept in this browser only: the server has no database'
      : log.error && entries.length
        ? `Could not load more: ${log.error.message}`
        : [log.loadedAt ? 'Read from the database' : '', log.loadedAt ? TimeZoneUtil.formatTime(new Date(log.loadedAt), undefined) : '',
          waiting ? `${pluralize(waiting, 'entry', 'entries')} waiting to be saved` : ''].filter(Boolean).join(' · ');
    byId('log-footer').hidden = !byId('log-source').textContent && !log.next;
    const more = byId('log-more');
    more.hidden = !log.next;
    more.disabled = Boolean(log.loading);
    more.classList.toggle('is-loading', log.loading === 'more');
  }
}
