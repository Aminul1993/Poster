/** The Activity log card. */
import { showToast } from '../app.js';
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
      const time = date.toDateString() === today
        ? date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })
        : date.toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
      return el('li', { class: 'log-item', dataset: { level: entry.level } }, [
        el('time', { class: 'log-time', datetime: entry.ts, title: date.toLocaleString(), text: time }),
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
