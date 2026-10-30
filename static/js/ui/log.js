/** The Activity log card. */
import { showToast } from '../app.js';
import { TimeZoneUtil } from '../time.js';
import { el } from '../util.js';
import { byId } from './dom.js';
import { View } from './view.js';

export class LogView extends View {
  constructor(ui) {
    super(ui);
    this.renderQueued = false;
  }

  bind() {
    byId('log-filter').addEventListener('change', (event) => {
      this.ui.prefs.logFilter = event.target.value;
      this.ui.saveUiPrefs();
      this.render();
    });
    byId('clear-log').addEventListener('click', async () => {
      const ok = await this.ui.confirm({ title: 'Clear the activity log?', message: 'All log entries are deleted from this browser.', confirmLabel: 'Clear log', danger: true });
      if (!ok) return;
      this.notifier.clearLog();
      showToast('Activity log cleared.', 'info');
    });
  }

  /** Batches log renders to one per frame. */
  queueRender() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => { this.renderQueued = false; this.render(); });
  }

  render() {
    const filter = this.ui.prefs.logFilter;
    const entries = this.notifier.entries;
    const filtered = entries.filter((entry) => {
      if (filter === 'all') return true;
      if (filter === 'warn') return entry.level === 'warn' || entry.category === 'retry';
      if (filter === 'user') return entry.category === 'user' || entry.category === 'settings' || entry.category === 'upload';
      return entry.level === filter;
    });
    const list = byId('log-list');
    const today = new Date().toDateString();
    list.replaceChildren(...filtered.slice().reverse().map((entry) => {
      const date = new Date(entry.ts);
      // Device time, in the dashboard's one format: "4:00:05 pm" today, "Fri, 30 Oct, 4:00 pm" before.
      const time = date.toDateString() === today ? TimeZoneUtil.formatTime(date, undefined, { seconds: true }) : TimeZoneUtil.format(date, undefined);
      return el('li', { class: 'log-item', dataset: { level: entry.level } }, [
        el('time', { class: 'log-time', datetime: entry.ts, title: TimeZoneUtil.format(date, undefined), text: time }),
        el('span', { class: 'log-cat', text: entry.category }),
        el('span', { class: 'log-msg' }, [entry.message, entry.details ? el('span', { class: 'log-detail', text: entry.details }) : null])
      ]);
    }));
    byId('log-count').textContent = String(entries.length);
    byId('clear-log').disabled = !entries.length;
    list.hidden = !filtered.length;
    byId('log-empty').hidden = filtered.length > 0;
    byId('log-empty-title').textContent = entries.length ? 'No matching activity' : 'No activity yet';
    byId('log-empty-text').textContent = entries.length
      ? 'Nothing in the log matches this filter.'
      : 'Uploads, generations, schedules, retries and errors are recorded here with timestamps.';
  }
}
