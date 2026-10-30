/**
 * UIController: owns the views, the header, KPIs, theme, banners and mode, and
 * reacts to store, scheduler, feed, notifier and storage events. Each card of
 * the dashboard is a view in this folder; views reach each other through the
 * controller's delegating methods (openSettings, confirm, renderEditor, ...).
 */
import { saveSettings, showToast } from '../app.js';
import { LANGUAGES, LIMITS, SERVER, STORAGE_KEYS, TIMING } from '../config.js';
import { POST_FILTERS } from '../services/buffer.js';
import { hasContent, isReady, needsTime } from '../store.js';
import { $, formatDuration, icon, pluralize } from '../util.js';
import { BulkEditView } from './bulk.js';
import { Dialogs } from './dialogs.js';
import { MOD_KEY, byId, hasFiles } from './dom.js';
import { EditorView } from './editor.js';
import { GalleryView } from './gallery.js';
import { ImageEditView } from './imageedit.js';
import { LogView } from './log.js';
import { OperationsView } from './operations.js';
import { Orchestrator } from './orchestrator.js';
import { PostsView, groupPosts } from './posts.js';
import { defaultPrefs, normalizePrefs } from './prefs.js';
import { SettingsView } from './settings.js';
import { UploadView } from './upload.js';

export class UIController {
  /** @param {typeof import('../app.js').app} appRef */
  constructor(appRef) {
    this.app = appRef;
    this.dialogs = new Dialogs();
    this.upload = new UploadView(this);
    this.imageEdit = new ImageEditView(this);
    this.gallery = new GalleryView(this);
    this.bulk = new BulkEditView(this);
    this.operations = new OperationsView(this);
    this.editor = new EditorView(this);
    this.posts = new PostsView(this);
    this.log = new LogView(this);
    this.settingsView = new SettingsView(this);
    this.orchestrator = new Orchestrator(this);
    this.health = { ollama: null, buffer: null };
    this.lastStorageToast = 0;
    this.opTicker = null;
    this.lastOp = null;
    /** @type {{count: number, more: boolean}|null} upcoming posts in Buffer, as last loaded */
    this.upcomingKpi = null;
    this.prefs = defaultPrefs();
  }

  get settings() { return this.app.settings; }
  get store() { return this.app.store; }
  get scheduler() { return this.app.scheduler; }
  get feed() { return this.app.feed; }
  get notifier() { return this.app.notifier; }
  get validation() { return this.app.validation; }

  /** Wires everything up and renders the first frame. */
  init() {
    this.applyTheme(this.currentTheme(), { persist: false });
    this.restoreUiPrefs();
    this.settingsView.populateTimezones();
    this.settingsView.populateLanguages();
    this.bindHeader();
    this.upload.bind();
    this.imageEdit.bind();
    this.gallery.bind();
    this.bulk.bind();
    this.operations.bind();
    this.editor.bind();
    this.posts.bind();
    this.log.bind();
    this.settingsView.bind();
    this.bindGlobal();
    this.bindShortcuts();
    this.bindServices();
    this.renderAll();
    this.showStartupBanners();
    this.feed.load();
  }

  /* ----------------------------------------------- delegates used by views */

  /** @param {string} [sectionId] @param {{title: string, message: string}} [reason] shown in the drawer */
  openSettings(sectionId, reason) { this.settingsView.open(sectionId, reason); }
  confirm(options) { return this.dialogs.confirm(options); }
  choose(options) { return this.dialogs.choose(options); }
  renderEditor() { this.editor.render(); }
  openPlanner(scope) { this.operations.openPlanner(scope); }
  /** @param {File[]} files */
  handleFiles(files) { return this.upload.handleFiles(files); }
  startGeneration(ids, scope) { return this.orchestrator.startGeneration(ids, scope); }
  startScheduling(ids, scope) { return this.orchestrator.startScheduling(ids, scope); }

  /* ---------------------------------------------------------------- theme */

  /** @returns {'light'|'dark'} */
  currentTheme() {
    const stored = this.app.storage.get(STORAGE_KEYS.theme, null);
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  /** @param {'light'|'dark'} theme @param {{persist?: boolean}} [options] */
  applyTheme(theme, { persist = true } = {}) {
    document.documentElement.setAttribute('data-theme', theme);
    const button = byId('theme-toggle');
    button.replaceChildren(icon(theme === 'dark' ? 'sun' : 'moon'));
    button.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0b0d12' : '#f5f6f8');
    if (persist) this.app.storage.set(STORAGE_KEYS.theme, theme);
  }

  toggleTheme() {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    this.applyTheme(next);
    this.notifier.log('info', 'user', `Switched to the ${next} theme.`);
  }

  /* ---------------------------------------------------------- preferences */

  restoreUiPrefs() {
    this.prefs = normalizePrefs(this.app.storage.get(STORAGE_KEYS.ui, {}));
    $(`input[name="image-source"][value="${this.prefs.imageSource}"]`).checked = true;
    byId('imggen-aspect').value = this.prefs.imageAspect;
    byId('log-filter').value = this.prefs.logFilter;
    byId('planner').open = this.prefs.plannerOpen;
    this.operations.showInterval(this.prefs.planInterval || this.settings.intervalMinutes);
    this.gallery.setView(this.prefs.galleryView);
  }

  saveUiPrefs() {
    this.app.storage.set(STORAGE_KEYS.ui, this.prefs);
  }

  /* ------------------------------------------------------------ rendering */

  renderAll() {
    this.renderHeader();
    this.renderKpis();
    this.upload.renderGenerator();
    this.imageEdit.render();
    this.gallery.render();
    this.operations.render();
    this.editor.render();
    this.posts.render();
    this.log.render();
    this.operations.renderPlannerHint();
  }

  /** Mode badge and service status pills. */
  renderHeader() {
    const s = this.settings;
    const demo = s.mode === 'demo';
    const badge = byId('mode-badge');
    badge.dataset.mode = s.mode;
    byId('mode-badge-label').textContent = demo ? 'Demo mode' : 'Live mode';
    badge.title = demo ? 'Simulated AI and scheduling. Click to switch to Live mode.' : 'Generating and scheduling through the Poster server. Click to switch to Demo mode.';
    badge.setAttribute('aria-label', `${demo ? 'Demo' : 'Live'} mode. Activate to switch to ${demo ? 'Live' : 'Demo'} mode.`);
    const setPill = (id, state) => {
      const pill = byId(id);
      pill.dataset.state = state.state;
      byId(`${id}-detail`).textContent = state.detail;
      pill.title = state.title;
      pill.setAttribute('aria-label', `${id === 'pill-ollama' ? 'Ollama' : 'Buffer'}: ${state.detail}. ${state.title}`);
    };
    setPill('pill-ollama', this.ollamaState());
    setPill('pill-buffer', this.bufferState());
    byId('pill-network').hidden = navigator.onLine !== false;
    // In Demo the badge already says it; the service labels would only repeat "Demo" twice more.
    byId('pill-ollama').hidden = demo;
    byId('pill-buffer').hidden = demo;
    byId('ai-sub').textContent = `${s.model}${demo ? ' (simulated)' : ''} · ${s.tone.charAt(0).toUpperCase()}${s.tone.slice(1)} tone · ${LANGUAGES[s.language] || 'English'}`;
  }

  /** @returns {{state: string, detail: string, title: string}} */
  ollamaState() {
    const s = this.settings;
    if (s.mode === 'demo') return { state: 'demo', detail: 'Demo', title: 'Simulated AI responses (Demo mode).' };
    if (this.health.ollama?.state === 'error') return { state: 'error', detail: 'Error', title: this.health.ollama.message };
    if (!SERVER.ollama.configured) return { state: 'warn', detail: 'Not configured', title: `Server: ${SERVER.ollama.problem}` };
    const host = SERVER.ollama.host || 'the server';
    if (this.health.ollama?.state === 'ok') return { state: 'ok', detail: 'Connected', title: `${s.model} at ${host}` };
    return { state: 'ok', detail: 'Ready', title: `${s.model} at ${host} (not tested yet)` };
  }

  /** @returns {{state: string, detail: string, title: string}} */
  bufferState() {
    const s = this.settings;
    if (s.mode === 'demo') return { state: 'demo', detail: 'Demo', title: `Simulated scheduling to ${pluralize(s.channelIds.demo.length, 'demo channel')}.` };
    if (this.health.buffer?.state === 'error') return { state: 'error', detail: 'Error', title: this.health.buffer.message };
    if (!SERVER.buffer.configured) return { state: 'warn', detail: 'No token', title: `Server: ${SERVER.buffer.problem}` };
    const selected = s.channelIds.live.length;
    const connection = this.app.liveBuffer.getConnection();
    if (!connection && !selected) return { state: 'warn', detail: 'Not connected', title: 'Open Settings → Buffer and connect to load channels.' };
    if (!selected) return { state: 'warn', detail: 'No channels', title: 'Choose at least one channel in Settings → Buffer.' };
    return { state: 'ok', detail: pluralize(selected, 'channel'), title: connection ? `Connected as ${connection.account?.email || 'Buffer user'}.` : 'Channels selected (connection not verified in this browser).' };
  }

  /** The batch line: images, captions, posts waiting for a time, posts ready, and what Buffer holds. */
  renderKpis() {
    const items = this.store.all();
    const generated = items.filter(hasContent).length;
    const ready = items.filter((i) => isReady(i, this.settings)).length;
    const waiting = items.filter((i) => needsTime(i, this.settings)).length;
    // Upcoming posts in Buffer, counted whenever the posts list includes them ("+" while more pages exist).
    if (this.feed.loaded && this.feed.filter !== 'published') {
      // The owner counts posts (images); Buffer counts one per channel, given in brackets.
      const upcoming = this.feed.posts.filter((p) => POST_FILTERS.upcoming.includes(p.status));
      this.upcomingKpi = { count: groupPosts(upcoming).length, copies: upcoming.length, more: Boolean(this.feed.next) };
    }
    byId('kpi-images').textContent = `${items.length} / ${LIMITS.maxImages}`;
    byId('kpi-generated').textContent = String(generated);
    byId('kpi-needs-time').textContent = String(waiting);
    byId('kpi-needs-time-label').textContent = waiting === 1 ? 'needs a publish time' : 'need a publish time';
    byId('kpi-needs-time-stat').classList.toggle('is-warning', waiting > 0);
    byId('kpi-ready').textContent = String(ready);
    const removed = items.filter((i) => i.result?.removed).length;
    byId('kpi-removed').textContent = String(removed);
    byId('kpi-removed-stat').hidden = !removed;
    const copies = this.upcomingKpi && this.upcomingKpi.copies !== this.upcomingKpi.count ? ` (${pluralize(this.upcomingKpi.copies, 'channel post')})` : '';
    byId('kpi-scheduled-label').textContent = this.settings.mode === 'demo' ? `upcoming in Demo mode, not in Buffer${copies}` : `upcoming in Buffer${copies}`;
    byId('kpi-scheduled-label').title = 'Buffer keeps one post per channel; Poster counts one per image.';
    byId('kpi-scheduled').textContent = this.upcomingKpi ? `${this.upcomingKpi.count}${this.upcomingKpi.more ? '+' : ''}` : '—';
    byId('capacity-bar').style.width = `${(items.length / LIMITS.maxImages) * 100}%`;
    byId('capacity-label').textContent = `${items.length} of ${LIMITS.maxImages} slots used`;
    const full = items.length >= LIMITS.maxImages;
    const dropzone = byId('dropzone');
    dropzone.classList.toggle('is-disabled', full);
    dropzone.setAttribute('aria-disabled', String(full));
    byId('gallery-count').textContent = String(items.length);
  }

  /* -------------------------------------------------------------- binding */

  bindHeader() {
    byId('theme-toggle').addEventListener('click', () => this.toggleTheme());
    byId('open-settings').addEventListener('click', () => this.openSettings());
    byId('mode-badge').addEventListener('click', () => this.setMode(this.settings.mode === 'demo' ? 'live' : 'demo'));
    window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', (event) => {
      const stored = this.app.storage.get(STORAGE_KEYS.theme, null);
      if (stored !== 'light' && stored !== 'dark') this.applyTheme(event.matches ? 'dark' : 'light', { persist: false });
    });
  }

  /** Global listeners: connectivity, page-level drop, unload guard. */
  bindGlobal() {
    // Toasts move to the other side while the owner works in Scheduled posts, so they never cover those rows.
    document.addEventListener('focusin', (event) => {
      byId('toast-region').classList.toggle('is-right', Boolean(event.target.closest?.('#schedules-section')));
    });
    window.addEventListener('offline', () => {
      this.renderHeader();
      this.notifier.banner('offline', {
        type: 'error',
        title: 'You are offline',
        message: 'Live AI generation and scheduling are paused until the connection returns. Demo mode keeps working.',
        dismissible: false
      });
      this.notifier.log('warn', 'system', 'Network connection lost.');
    });
    window.addEventListener('online', () => {
      this.renderHeader();
      this.notifier.clearBanner('offline');
      showToast('Back online.', 'success');
      this.notifier.log('info', 'system', 'Network connection restored.');
    });
    window.addEventListener('dragover', (event) => {
      if (hasFiles(event)) event.preventDefault();
    });
    window.addEventListener('drop', (event) => {
      if (!hasFiles(event)) return;
      const handled = event.defaultPrevented;
      event.preventDefault();
      if (!handled && !byId('settings-dialog').open) this.handleFiles(Array.from(event.dataTransfer.files || []));
    });
    window.addEventListener('beforeunload', (event) => {
      if (this.scheduler.isRunning) {
        event.preventDefault();
        event.returnValue = '';
      }
      this.notifier.persistLog.flush();
      this.store.persistDrafts.flush();
    });
  }

  /**
   * Ctrl+Shift+Enter (⌘ on a Mac) runs the batch's next step; "?" lists every shortcut. Neither acts while a
   * dialog or the drawer is open. "?" never acts in a field; the next step works from the planner's time and
   * interval fields (where a run's end puts the cursor), but not where Enter writes text.
   */
  bindShortcuts() {
    for (const key of document.querySelectorAll('.mod-key')) key.textContent = MOD_KEY.label;
    byId('open-shortcuts').addEventListener('click', () => this.openShortcuts());
    const typing = (target) => target instanceof HTMLElement && (target.matches('textarea, select, [contenteditable="true"]')
      || (target.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(target.type)));
    const writing = (target) => target instanceof HTMLElement && target.matches('textarea, [contenteditable="true"], input:not([type]), input[type="text"], input[type="url"], input[type="search"]');
    document.addEventListener('keydown', (event) => {
      if (event.defaultPrevented || event.isComposing || event.altKey || document.querySelector('dialog[open]')) return;
      if (event.key === 'Enter' && event.shiftKey && (MOD_KEY.aria === 'Meta' ? event.metaKey : event.ctrlKey)) {
        if (writing(event.target)) return;
        event.preventDefault();
        this.operations.runNextStep();
      } else if (event.key === '?' && !event.ctrlKey && !event.metaKey && !typing(event.target)) {
        event.preventDefault();
        this.openShortcuts();
      }
    });
  }

  openShortcuts() {
    byId('shortcuts-dialog').showModal();
    byId('shortcuts-close').focus();
  }

  /** Reacts to store, scheduler, notifier and storage events. */
  bindServices() {
    this.store.addEventListener('change', (event) => {
      const { reason, ids } = event.detail;
      if (reason === 'edit') {
        // Typing in the editor: only the active card's status and the counts can change.
        for (const id of ids) this.gallery.renderCard(id);
        this.renderKpis();
        this.operations.render();
        return;
      }
      this.gallery.render();
      this.renderKpis();
      this.upload.renderGenerator();
      this.imageEdit.render();
      this.operations.render();
      const touchesActive = ids.includes(this.store.activeId) || ['active', 'remove', 'clear', 'add', 'reorder'].includes(reason);
      if (reason !== 'edit' && touchesActive) this.editor.render();
      if (['planned', 'add', 'remove', 'clear', 'active'].includes(reason)) this.operations.compactStatus();
      if (reason === 'reorder') this.gallery.announceMove(ids[0]);
    });
    this.scheduler.addEventListener('start', (event) => {
      this.lastOp = event.detail;
      this.operations.renderStatus(event.detail);
      this.focusRunStart();
      this.operations.render();
      this.editor.render();
      clearInterval(this.opTicker);
      this.opTicker = setInterval(() => { if (this.scheduler.current) this.operations.renderStatus(this.scheduler.current); }, TIMING.opTickerMs);
    });
    this.scheduler.addEventListener('progress', (event) => this.operations.renderStatus(event.detail));
    this.scheduler.addEventListener('complete', (event) => {
      clearInterval(this.opTicker);
      this.opTicker = null;
      this.onOperationComplete(event.detail);
    });
    this.feed.addEventListener('change', () => { this.posts.render(); this.renderKpis(); });
    this.notifier.addEventListener('log', () => this.log.queueRender());
    this.app.storage.addEventListener('storage-error', (event) => this.onStorageError(event.detail));
    this.app.storage.addEventListener('storage-warning', (event) => showToast(event.detail.message, 'warning'));
  }

  /** @param {import('../scheduler.js').Operation} op */
  onOperationComplete(op) {
    this.operations.renderStatus(op);
    // Captions are ready but nothing can be scheduled without a time: the planner is the next step.
    if (op.kind === 'generate' && op.succeeded && this.store.all().some((i) => needsTime(i, this.settings))) {
      byId('planner').open = true;
      // The planner is the next step; the finished run shrinks so it does not push the editor down.
      this.operations.compactStatus();
    }
    if (op.kind === 'schedule' && op.succeeded) byId('planner').open = false;
    this.renderAll();
    // Show the new posts as Buffer has them: one reload per scheduling run.
    if (op.kind === 'schedule' && op.succeeded) this.feed.load();
    const isGen = op.kind === 'generate';
    const service = isGen ? 'ollama' : 'buffer';
    const live = this.settings.mode === 'live';
    const stats = `${op.succeeded} succeeded · ${op.failed} failed · ${op.skipped} skipped · ${op.cancelled} not processed · ${op.retries} retries · ${formatDuration(op.finishedAt - op.startedAt)}`;
    if (live && op.succeeded) this.health[service] = { state: 'ok' };
    if (live && op.abortReason && ['auth', 'network', 'config'].includes(op.abortReason.kind)) {
      this.health[service] = { state: 'error', message: op.abortReason.message };
      this.showServiceErrorBanner(op.abortReason);
    }
    this.renderHeader();

    const noun = isGen ? 'image' : 'post';
    if (op.abortReason) {
      showToast(op.abortReason.message, 'error', { title: `${isGen ? 'Generation' : 'Scheduling'} stopped` });
      this.notifier.log('error', isGen ? 'ai' : 'schedule', `${op.label} stopped: ${op.abortReason.message}`, stats);
    } else if (op.state === 'cancelled') {
      showToast(`${isGen ? 'Generation' : 'Scheduling'} cancelled after ${op.succeeded} of ${pluralize(op.total, noun)}.`, 'warning');
      this.notifier.log('warn', isGen ? 'ai' : 'schedule', `${op.label} cancelled.`, stats);
    } else if (op.failed) {
      showToast(`${op.succeeded} of ${pluralize(op.total, noun)} ${isGen ? 'generated' : 'scheduled'}; ${op.failed} failed. The reasons are listed under the progress bar.`, op.succeeded ? 'warning' : 'error', { title: `${isGen ? 'Generation' : 'Scheduling'} finished with errors` });
      this.notifier.log(op.succeeded ? 'warn' : 'error', isGen ? 'ai' : 'schedule', `${op.label} finished with errors.`, stats);
    } else {
      const verb = isGen ? 'Generated content for' : 'Scheduled';
      const where = !isGen && op.demo ? ' in Demo mode (not in Buffer)' : '';
      const title = isGen ? 'Content ready' : op.demo ? 'Scheduled in Demo mode' : 'Posts scheduled';
      // The progress panel already says this when it is on screen; a toast would only cover the page.
      if (!this.operations.statusInView()) showToast(`${verb} ${pluralize(op.succeeded, noun)}${where}${op.skipped ? ` (${op.skipped} skipped)` : ''}${op.retries ? ` after ${pluralize(op.retries, 'retry', 'retries')}` : ''}.`, 'success', { title });
      this.notifier.log('success', isGen ? 'ai' : 'schedule', `${op.label} complete.`, stats);
    }
    this.focusNextStep(op);
  }

  /** A run's start puts keyboard focus on its progress title, unless the owner is working elsewhere. */
  focusRunStart() {
    const active = document.activeElement;
    if (!active || active === document.body || byId('ai-section').contains(active)) byId('op-title').focus({ preventScroll: true });
  }

  /** A run's end puts focus on the next step: the planner, All ready, or the closing line's View button. */
  focusNextStep(op) {
    const active = document.activeElement;
    if (active && active !== document.body && active.id !== 'op-title') return;
    const stage = this.operations.stage();
    let target = byId('op-title');
    if (op.kind === 'schedule' && !byId('op-view').hidden) target = byId('op-view');
    else if (stage === 'plan') target = byId('planner').open ? byId('plan-start') : byId('sched-needs-time');
    else if (stage === 'schedule') target = byId('sched-all');
    if (!target.disabled && !target.hidden) target.focus({ preventScroll: true });
  }

  /** Banner for errors that need user action (auth, configuration, network). */
  showServiceErrorBanner(error) {
    const service = error.service || 'Service';
    const section = service === 'Ollama' ? 'settings-ollama' : service === 'Media host' ? 'settings-media' : 'settings-buffer';
    const actions = [{ label: 'Open settings', onClick: () => this.openSettings(section) }];
    if (this.settings.mode === 'live') actions.push({ label: 'Use Demo mode', onClick: () => this.setMode('demo'), primary: true });
    this.notifier.banner(`service-${service.toLowerCase().replace(/\W+/g, '-')}`, {
      type: 'error', title: error.message, message: error.hint || '', actions
    });
  }

  onStorageError({ op, name, error }) {
    const now = Date.now();
    if (now - this.lastStorageToast < TIMING.storageToastGapMs) return;
    this.lastStorageToast = now;
    const message = op === 'quota'
      ? 'Browser storage is full. Changes are kept for this session only. Clear saved drafts or the activity log in Settings to free space.'
      : op === 'parse'
        ? `Saved ${name} data was unreadable and has been reset.`
        : `Could not ${op === 'read' ? 'read from' : 'write to'} browser storage (${error?.name || 'unknown error'}). Changes are kept for this session only.`;
    showToast(message, 'warning', { title: 'Storage problem' });
    if (name !== 'log') this.notifier.log('warn', 'system', message);
  }

  showStartupBanners() {
    if (!this.app.storage.persistent) {
      this.notifier.banner('storage', {
        type: 'warning',
        title: 'Browser storage is unavailable',
        message: 'Settings and drafts work for this session but are lost when you close the tab (private mode or blocked site data).'
      });
    }
    if (navigator.onLine === false) {
      this.notifier.banner('offline', { type: 'error', title: 'You are offline', message: 'Live generation and scheduling need a connection. Demo mode works offline.', dismissible: false });
    }
    this.updateModeBanner();
  }

  /** Demo-mode notice, or configuration problems in Live mode. */
  updateModeBanner() {
    const s = this.settings;
    this.notifier.clearBanner('mode');
    if (s.mode === 'demo') {
      const ready = SERVER.ollama.configured;
      this.notifier.banner('mode', {
        type: 'info',
        title: 'You are in Demo mode',
        message: ready
          ? 'AI captions and Buffer scheduling are simulated. The server is configured, so you can switch to Live mode whenever you are ready.'
          : 'AI captions and Buffer scheduling are simulated, so you can try everything safely. To go live, add OLLAMA_API_KEY and BUFFER_ACCESS_TOKEN to the .env file next to app.py and restart Poster.',
        actions: ready
          ? [{ label: 'Switch to Live mode', onClick: () => this.setMode('live') }]
          : [{ label: 'Open settings', onClick: () => this.openSettings() }]
      });
      return;
    }
    const result = this.validation.validateSettings(s, { requireCredentials: true });
    const problems = [...Object.values(result.errors), ...result.warnings];
    if (problems.length) {
      this.notifier.banner('mode', {
        type: result.valid ? 'info' : 'warning',
        title: result.valid ? 'Live mode: one more step' : 'Live mode needs attention',
        message: problems.slice(0, 2).join(' '),
        actions: [{ label: 'Open settings', onClick: () => this.openSettings() }]
      });
    }
  }

  /**
   * Switches between Demo and Live mode (Live requires valid AI settings and a confirmation, since posts
   * then go to real accounts).
   * @param {'demo'|'live'} mode
   */
  async setMode(mode) {
    if (this.scheduler.isRunning) {
      showToast('Wait for the current operation to finish before switching modes.', 'warning');
      return;
    }
    if (mode === this.settings.mode) return;
    if (mode === 'live') {
      const check = this.validation.validateSettings({ ...this.settings, mode: 'live' }, { scope: 'ollama', requireCredentials: true });
      if (!check.valid) {
        this.openSettings('settings-ollama', { title: 'Live mode needs server configuration', message: Object.values(check.errors)[0] });
        return;
      }
      const ok = await this.confirmLive(this.settings);
      if (!ok || this.settings.mode === 'live') return;
    }
    const result = saveSettings({ ...this.settings, mode });
    if (!result.ok) {
      this.openSettings(undefined, { title: 'Could not switch mode', message: Object.values(result.errors)[0] });
      return;
    }
    for (const id of ['service-ollama', 'service-buffer', 'service-media-host']) this.notifier.clearBanner(id);
    showToast(mode === 'demo' ? 'Demo mode: AI and scheduling are simulated.' : 'Live mode: requests go to Ollama and Buffer.', 'info', { title: `Switched to ${mode === 'demo' ? 'Demo' : 'Live'} mode` });
  }

  /**
   * Going Live creates real posts, so it is confirmed the same way from the header badge and from Settings.
   * @param {object} settings the settings that will apply @returns {Promise<boolean>}
   */
  confirmLive(settings) {
    const live = { ...settings, mode: 'live' };
    const channels = this.app.liveBuffer.selectedChannelIds(live).map((id) => this.app.liveBuffer.channelName(id, live));
    const rehearsed = this.store.all().filter((i) => i.result?.mode === 'demo').length;
    return this.confirm({
      title: 'Switch to Live mode?',
      message: [
        `Captions will come from ${settings.model} on Ollama, and scheduling creates real posts in Buffer on ${channels.length ? channels.join(', ') : 'the channels you choose in Settings → Buffer'}.`,
        rehearsed ? `${pluralize(rehearsed, 'post')} scheduled in Demo mode ${rehearsed === 1 ? 'is' : 'are'} not in Buffer, so ${rehearsed === 1 ? 'it' : 'they'} can be scheduled for real.` : ''
      ].filter(Boolean).join('\n\n'),
      confirmLabel: 'Switch to Live',
      symbol: 'zap',
      focusCancel: true
    });
  }

  /** Called by saveSettings() after settings change. */
  onSettingsChanged(previous, next) {
    if (!previous) return;
    if (previous.mode !== next.mode) {
      this.notifier.log('info', 'settings', `Switched to ${next.mode === 'demo' ? 'Demo' : 'Live'} mode.`);
      this.upload.showGeneratorError('');
      this.upcomingKpi = null;
      this.feed.load();
    }
    if (previous.model !== next.model) this.health.ollama = null;
    if (previous.timezone !== next.timezone) byId('plan-start').value = '';
    this.renderAll();
    this.updateModeBanner();
  }
}
