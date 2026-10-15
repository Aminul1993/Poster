/** The Generate / Schedule buttons, the progress panel and the schedule planner. */
import {
  cancelScheduledOperation, generateContentForImage, generateForAllImages, generateForSelectedImages, scheduleAllPosts,
  scheduleSelectedPosts, scheduleSinglePost, showToast
} from '../app.js';
import { hasContent } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { el, formatDuration, icon, pluralize, truncate } from '../util.js';
import { byId } from './dom.js';
import { View } from './view.js';

export class OperationsView extends View {
  render() {
    const items = this.store.all();
    const running = this.scheduler.isRunning;
    const active = this.store.active;
    const selected = this.store.selected();
    const schedulable = (list) => list.filter((i) => hasContent(i) && !i.result);
    byId('gen-active').disabled = running || !active;
    byId('gen-selected').disabled = running || !selected.length;
    byId('gen-all').disabled = running || !items.length;
    byId('gen-selected-count').textContent = String(selected.length);
    byId('gen-all-count').textContent = String(items.length);
    byId('sched-active').disabled = running || !active;
    byId('sched-selected').disabled = running || !selected.length;
    byId('sched-all').disabled = running || !schedulable(items).length;
    byId('sched-selected-count').textContent = String(schedulable(selected).length);
    byId('sched-all-count').textContent = String(schedulable(items).length);
    byId('plan-selected').disabled = !selected.length;
    byId('plan-all').disabled = !items.length;
    byId('clear-images').disabled = running || !items.length;
  }

  /**
   * Progress panel for the running (or last) operation.
   * @param {import('../scheduler.js').Operation} op
   */
  renderStatus(op) {
    const box = byId('op-status');
    box.hidden = false;
    const finished = Boolean(op.finishedAt);
    const isGen = op.kind === 'generate';
    const pct = op.total ? Math.round((op.done / op.total) * 100) : 0;
    let state = 'running';
    if (finished) state = op.state === 'done' ? 'done' : op.state === 'cancelled' ? 'cancelled' : 'failed';
    box.dataset.state = state;

    let title;
    if (!finished) title = `${isGen ? 'Generating content' : 'Scheduling posts'} · ${op.done} of ${op.total}`;
    else if (op.state === 'done') title = isGen ? 'Content generation complete' : 'Scheduling complete';
    else if (op.state === 'cancelled') title = isGen ? 'Generation cancelled' : 'Scheduling cancelled';
    else if (op.abortReason) title = 'Stopped after an error';
    else if (op.state === 'partial') title = isGen ? 'Generation finished with errors' : 'Scheduling finished with errors';
    else title = isGen ? 'Generation failed' : 'Scheduling failed';
    byId('op-title').textContent = title;
    const iconBox = byId('op-icon');
    if (!finished) {
      if (!iconBox.querySelector('.spinner')) iconBox.replaceChildren(el('span', { class: 'spinner' }));
    } else {
      iconBox.replaceChildren(icon(state === 'done' ? 'check-circle' : state === 'cancelled' ? 'stop' : 'alert-circle'));
    }

    const progress = byId('op-progress');
    progress.setAttribute('aria-valuenow', String(pct));
    progress.setAttribute('aria-valuetext', `${op.done} of ${op.total} processed`);
    progress.classList.toggle('is-indeterminate', !finished && op.done === 0 && op.active.length > 0 && op.total === 1);
    byId('op-bar').style.width = `${pct}%`;

    let current;
    if (!finished) {
      if (op.cancelRequested) current = isGen ? 'Cancelling…' : 'Cancelling… letting the post already sent to Buffer finish.';
      else if (op.active.length) current = `Working on ${op.active.map((n) => `"${truncate(n, 40)}"`).join(', ')}`;
      else current = 'Starting…';
    } else {
      current = `Finished in ${formatDuration(op.finishedAt - op.startedAt)}.`;
      if (op.abortReason) current += ` ${op.abortReason.message}`;
    }
    byId('op-current').textContent = current;

    const elapsed = formatDuration((op.finishedAt || Date.now()) - op.startedAt);
    const stat = (label, value) => el('span', {}, [`${label} `, el('strong', { text: String(value) })]);
    // replaceChildren() would render null as text, so optional nodes are filtered out.
    byId('op-stats').replaceChildren(...[
      stat(isGen ? 'Generated' : 'Scheduled', op.succeeded),
      stat('Failed', op.failed),
      op.skipped ? stat('Skipped', op.skipped) : null,
      op.cancelled ? stat(finished ? 'Not processed' : 'Cancelled', op.cancelled) : null,
      stat('Retries', op.retries),
      stat('Elapsed', elapsed)
    ].filter(Boolean));
    const errors = byId('op-errors');
    errors.hidden = !op.errors.length;
    errors.replaceChildren(...[
      ...op.errors.slice(0, 8).map((e) => el('li', { text: `${e.name}: ${e.message}` })),
      op.errors.length > 8 ? el('li', { text: `…and ${op.errors.length - 8} more (see the activity log).` }) : null
    ].filter(Boolean));
    byId('cancel-op').hidden = finished;
    byId('cancel-op').disabled = op.cancelRequested;
    byId('dismiss-op').hidden = !finished;
  }

  bind() {
    byId('gen-active').addEventListener('click', () => generateContentForImage());
    byId('gen-selected').addEventListener('click', () => generateForSelectedImages());
    byId('gen-all').addEventListener('click', () => generateForAllImages());
    byId('sched-active').addEventListener('click', () => scheduleSinglePost());
    byId('sched-selected').addEventListener('click', () => scheduleSelectedPosts());
    byId('sched-all').addEventListener('click', () => scheduleAllPosts());
    byId('cancel-op').addEventListener('click', () => {
      if (cancelScheduledOperation()) showToast('Cancelling the current operation…', 'info');
    });
    byId('dismiss-op').addEventListener('click', () => { byId('op-status').hidden = true; });
    byId('planner').addEventListener('toggle', () => { this.ui.prefs.plannerOpen = byId('planner').open; this.ui.saveUiPrefs(); });
    byId('plan-interval').addEventListener('change', () => {
      const value = Number(byId('plan-interval').value);
      if (Number.isInteger(value) && value > 0) { this.ui.prefs.planInterval = value; this.ui.saveUiPrefs(); }
    });
    byId('plan-selected').addEventListener('click', () => this.applyPlanner('selected'));
    byId('plan-all').addEventListener('click', () => this.applyPlanner('all'));
  }

  /** @param {'selected'|'all'} scope */
  applyPlanner(scope) {
    const items = scope === 'selected' ? this.store.selected() : this.store.all();
    if (!items.length) {
      showToast(scope === 'selected' ? 'Select images in the gallery first.' : 'Upload images first.', 'info');
      return;
    }
    const interval = Number(byId('plan-interval').value);
    if (!Number.isInteger(interval) || interval < 1 || interval > 10080) {
      showToast('The interval must be a whole number of minutes from 1 to 10080.', 'error');
      byId('plan-interval').focus();
      return;
    }
    const start = byId('plan-start').value;
    const check = this.validation.validateSchedule(start, this.settings);
    if (!check.valid) {
      showToast(check.error, 'error', { title: 'Check the first post time' });
      byId('plan-start').focus();
      return;
    }
    const overwrite = byId('plan-overwrite').checked;
    const count = this.scheduler.planTimes(items, start, interval, overwrite);
    if (!count) {
      showToast('No times changed: these posts already have times or are scheduled. Tick "Replace times already set" to overwrite.', 'info');
      return;
    }
    this.notifier.log('info', 'user', `Planned publish times for ${pluralize(count, 'post')}, every ${pluralize(interval, 'minute')} from ${TimeZoneUtil.format(check.date, this.settings.timezone)}.`);
    showToast(`Publish times set for ${pluralize(count, 'post')}.`, 'success');
    this.ui.renderEditor();
  }

  renderPlannerHint() {
    const s = this.settings;
    byId('plan-tz-hint').textContent = `Times use ${s.timezone} and follow the gallery order. Already-scheduled posts are not changed.`;
    const start = byId('plan-start');
    if (!start.value) {
      const nextHour = new Date(Math.ceil((Date.now() + 30 * 60000) / 3600000) * 3600000);
      start.value = TimeZoneUtil.toLocalInput(nextHour, s.timezone);
    }
  }
}
