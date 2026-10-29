/** The Generate / Schedule buttons, the progress panel and the schedule planner. */
import {
  cancelScheduledOperation, generateForAllImages, generateForSelectedImages, scheduleAllPosts, scheduleSelectedPosts, showToast
} from '../app.js';
import { LIMITS } from '../config.js';
import { countedResult, hasContent, isReady, needsTime } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { el, formatDuration, icon, pluralize, truncate } from '../util.js';
import { byId } from './dom.js';
import { View } from './view.js';

/** Planner interval units, in minutes. */
const UNITS = [[1440, 'days'], [60, 'hours'], [1, 'minutes']];

export class OperationsView extends View {
  /**
   * The step the batch is at, which decides the one indigo button in the panel: captions first, then times,
   * then scheduling. 'done' when nothing is left to do.
   * @returns {'empty'|'generate'|'plan'|'schedule'|'done'}
   */
  stage() {
    const items = this.store.all();
    if (!items.length) return 'empty';
    const { mode } = this.settings;
    if (items.some((i) => !hasContent(i) && !countedResult(i, mode))) return 'generate';
    if (items.some((i) => needsTime(i, this.settings))) return 'plan';
    if (items.some((i) => isReady(i, this.settings))) return 'schedule';
    return 'done';
  }

  render() {
    const items = this.store.all();
    const running = this.scheduler.isRunning;
    const selected = this.store.selected();
    const readyAll = items.filter((i) => isReady(i, this.settings)).length;
    const readySelected = selected.filter((i) => isReady(i, this.settings)).length;
    const waiting = items.filter((i) => needsTime(i, this.settings)).length;
    const stage = this.stage();
    byId('gen-all').classList.toggle('btn-primary', stage === 'generate');
    byId('plan-all').classList.toggle('btn-primary', stage === 'plan');
    byId('sched-all').classList.toggle('btn-primary', stage === 'schedule');
    byId('gen-selected').disabled = running || !selected.length;
    byId('gen-all').disabled = running || !items.length;
    byId('gen-selected-count').textContent = String(selected.length);
    byId('gen-all-count').textContent = String(items.length);
    byId('gen-selected').setAttribute('aria-label', `Generate content for ${pluralize(selected.length, 'selected image')}`);
    byId('gen-all').setAttribute('aria-label', `Generate content for all ${pluralize(items.length, 'image')}`);
    byId('sched-selected').disabled = running || !selected.length;
    byId('sched-all').disabled = running || !readyAll;
    // "Selected" acts on every selected post; when some are not ready the count says so instead of hiding them.
    byId('sched-selected-count').textContent = readySelected === selected.length ? String(selected.length) : `${readySelected} of ${selected.length} ready`;
    byId('sched-all-count').textContent = String(readyAll);
    byId('sched-selected').setAttribute('aria-label', `Schedule selected posts: ${readySelected} of ${selected.length} ready`);
    byId('sched-all').setAttribute('aria-label', `Schedule all ${pluralize(readyAll, 'ready post')}`);
    const needs = byId('sched-needs-time');
    needs.hidden = !waiting || byId('planner').open;
    byId('sched-needs-time-label').textContent = `${waiting === 1 ? '1 post needs' : `${waiting} posts need`} a publish time`;
    byId('plan-selected').disabled = !selected.length;
    byId('plan-all').disabled = !items.length;
    byId('clear-images').disabled = running || !items.length;
    this.renderPlannerPreview();
  }

  /** @param {import('../scheduler.js').Operation} op a finished scheduling run @returns {string} the closing line */
  describeQueued(op) {
    const zone = this.settings.timezone;
    const times = [...op.dueTimes].sort((a, b) => a - b);
    const format = (ms) => TimeZoneUtil.format(new Date(ms), zone);
    const where = op.demo ? ' in Demo mode (not in Buffer)' : ' in Buffer';
    const span = times.length === 1 || times[0] === times[times.length - 1]
      ? `publishes ${format(times[0])}`
      : `first ${format(times[0])}, last ${format(times[times.length - 1])}`;
    const night = times.filter((ms) => TimeZoneUtil.isNight(new Date(ms), zone)).length;
    const late = night ? ` ${pluralize(night, 'post')} ${night === 1 ? 'lands' : 'land'} between 11 pm and 6 am.` : '';
    return `${pluralize(op.succeeded, 'post')} queued${where} · ${span}.${late}`;
  }

  /** @returns {boolean} whether the progress panel is on screen, so a toast would only repeat it */
  statusInView() {
    const box = byId('op-status');
    if (box.hidden) return false;
    const rect = box.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < window.innerHeight;
  }

  /** Once the batch moves on, the finished run's panel shrinks to its title and closing line. */
  compactStatus() {
    const box = byId('op-status');
    if (!box.hidden && !this.scheduler.isRunning && box.dataset.state !== 'running') box.classList.add('is-compact');
  }

  /** The planner's first post: the time of day used last (9:00 at first), today if still ahead, otherwise tomorrow. */
  defaultStart() {
    const zone = this.settings.timezone;
    const timeOfDay = this.ui.prefs.planTimeOfDay || '09:00';
    const today = `${TimeZoneUtil.toLocalInput(new Date(), zone).slice(0, 10)}T${timeOfDay}`;
    const candidate = TimeZoneUtil.zonedToUtc(today, zone);
    if (candidate && candidate.date.getTime() > Date.now() + 30 * 60000) return today;
    return `${TimeZoneUtil.toLocalInput(new Date(Date.now() + 864e5), zone).slice(0, 10)}T${timeOfDay}`;
  }

  /** Opens the planner and puts the cursor in its first field. */
  openPlanner() {
    const planner = byId('planner');
    planner.open = true;
    planner.scrollIntoView({ block: 'nearest' });
    byId('plan-start').focus();
  }

  /** @returns {number} the planner interval in minutes (NaN when not a whole number) */
  intervalMinutes() {
    const value = Number(byId('plan-interval').value);
    return Number.isInteger(value) ? value * Number(byId('plan-unit').value) : NaN;
  }

  /** @param {number} minutes shows a stored interval in the largest unit that divides it */
  showInterval(minutes) {
    const [unit] = UNITS.find(([size]) => minutes % size === 0) || UNITS[UNITS.length - 1];
    byId('plan-unit').value = String(unit);
    byId('plan-interval').value = String(minutes / unit);
  }

  /** What "Apply to all" would do: how many posts get a time, and the first and last of them. */
  renderPlannerPreview() {
    const preview = byId('plan-preview');
    preview.classList.remove('is-warning');
    const s = this.settings;
    const count = this.scheduler.plannable(this.store.all(), byId('plan-overwrite').checked).length;
    const start = TimeZoneUtil.zonedToUtc(byId('plan-start').value, s.timezone);
    const minutes = this.intervalMinutes();
    if (!count) {
      preview.textContent = this.store.count ? 'Every post already has a time. Tick "Replace times already set" to plan them again.' : '';
      return;
    }
    if (!start || !(minutes >= 1 && minutes <= LIMITS.maxPlanMinutes)) {
      preview.textContent = `${pluralize(count, 'post')} without a time.`;
      return;
    }
    const format = (date) => TimeZoneUtil.format(date, s.timezone);
    const last = new Date(start.date.getTime() + (count - 1) * minutes * 60000);
    preview.textContent = count === 1
      ? `Apply to all: 1 post at ${format(start.date)}.`
      : `Apply to all: ${count} posts, from ${format(start.date)} to ${format(last)}.`;
    // A long run at a short interval can fall in the night; say so before it is applied.
    const night = Array.from({ length: count }, (_, i) => TimeZoneUtil.parts(new Date(start.date.getTime() + i * minutes * 60000), s.timezone).hour)
      .filter((hour) => hour >= 23 || hour < 6).length;
    preview.classList.toggle('is-warning', night > 0);
    if (night) preview.textContent += ` ${night === 1 ? '1 of them lands' : `${night} of them land`} between 11 pm and 6 am.`;
  }

  /**
   * Progress panel for the running (or last) operation.
   * @param {import('../scheduler.js').Operation} op
   */
  renderStatus(op) {
    const box = byId('op-status');
    box.hidden = false;
    box.classList.remove('is-compact');
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
    } else if (!isGen && op.succeeded && op.dueTimes?.length) {
      current = this.describeQueued(op);
    } else {
      current = `Finished in ${formatDuration(op.finishedAt - op.startedAt)}.`;
      if (op.abortReason) current += ` ${op.abortReason.message}`;
    }
    // The ticker re-renders every second; rewriting identical text would make screen readers repeat it.
    if (byId('op-current').textContent !== current) byId('op-current').textContent = current;
    byId('op-view').hidden = !(finished && !isGen && op.succeeded);
    // Screen readers hear a run start and its outcome, not every item and every second.
    const phase = `${op.id}:${finished}`;
    if (this.announced !== phase) {
      this.announced = phase;
      byId('op-announce').textContent = finished ? `${title}. ${current}` : `${isGen ? 'Generating content for' : 'Scheduling'} ${pluralize(op.total, isGen ? 'image' : 'post')}.`;
    }

    const elapsed = formatDuration((op.finishedAt || Date.now()) - op.startedAt);
    const stat = (label, value) => el('span', {}, [`${label} `, el('strong', { text: String(value) })]);
    // replaceChildren() would render null as text, so optional nodes are filtered out.
    byId('op-stats').replaceChildren(...[
      stat(isGen ? 'Generated' : 'Scheduled', op.succeeded),
      stat('Failed', op.failed),
      op.skipped ? stat('Skipped', op.skipped) : null,
      op.cancelled ? stat(finished ? 'Not processed' : 'Cancelled', op.cancelled) : null,
      stat('Retries', op.retries),
      finished ? null : stat('Elapsed', elapsed)
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
    byId('gen-selected').addEventListener('click', () => generateForSelectedImages());
    byId('gen-all').addEventListener('click', () => generateForAllImages());
    byId('sched-selected').addEventListener('click', () => scheduleSelectedPosts());
    byId('sched-all').addEventListener('click', () => scheduleAllPosts());
    byId('sched-needs-time').addEventListener('click', () => this.openPlanner());
    byId('cancel-op').addEventListener('click', () => {
      if (cancelScheduledOperation()) showToast('Cancelling the current operation…', 'info');
    });
    byId('dismiss-op').addEventListener('click', () => { byId('op-status').hidden = true; });
    byId('op-view').addEventListener('click', (event) => {
      event.preventDefault();
      this.ui.posts.showNew(this.ui.lastOp?.postIds || []);
      const target = byId('schedules-section');
      target.scrollIntoView({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      byId('sched-title').setAttribute('tabindex', '-1');
      byId('sched-title').focus({ preventScroll: true });
    });
    byId('planner').addEventListener('toggle', () => { this.ui.prefs.plannerOpen = byId('planner').open; this.ui.saveUiPrefs(); this.render(); });
    const savePlanInterval = () => {
      const minutes = this.intervalMinutes();
      if (minutes >= 1 && minutes <= LIMITS.maxPlanMinutes) { this.ui.prefs.planInterval = minutes; this.ui.saveUiPrefs(); }
      this.renderPlannerPreview();
    };
    byId('plan-interval').addEventListener('input', savePlanInterval);
    byId('plan-unit').addEventListener('change', savePlanInterval);
    byId('plan-start').addEventListener('input', () => this.renderPlannerPreview());
    byId('plan-overwrite').addEventListener('change', () => this.renderPlannerPreview());
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
    const interval = this.intervalMinutes();
    if (!(interval >= 1 && interval <= LIMITS.maxPlanMinutes)) {
      showToast('Use a whole number, from 1 minute up to 7 days.', 'error', { title: 'Check the interval' });
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
    this.notifier.log('info', 'user', `Planned publish times for ${pluralize(count, 'post')}, every ${this.describeInterval(interval)} from ${TimeZoneUtil.format(check.date, this.settings.timezone)}.`);
    showToast(`Publish times set for ${pluralize(count, 'post')}.`, 'success');
    // Planning is done: the planner folds away so the editor moves up for review.
    byId('planner').open = false;
    this.ui.prefs.planTimeOfDay = start.slice(11, 16);
    this.ui.saveUiPrefs();
    this.ui.renderEditor();
  }

  /** @param {number} minutes @returns {string} e.g. "2 hours" */
  describeInterval(minutes) {
    const [size, unit] = UNITS.find(([s]) => minutes % s === 0) || UNITS[UNITS.length - 1];
    return pluralize(minutes / size, unit.slice(0, -1), unit);
  }

  renderPlannerHint() {
    const s = this.settings;
    byId('plan-tz-hint').textContent = `Times use ${s.timezone} and follow the gallery order. Already-scheduled posts are not changed.`;
    const start = byId('plan-start');
    if (!start.value) start.value = this.defaultStart();
    this.renderPlannerPreview();
  }
}
