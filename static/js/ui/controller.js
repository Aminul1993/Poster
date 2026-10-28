/**
 * UIController: owns the views, the header, KPIs, theme, banners and mode, and
 * reacts to store, scheduler, feed, notifier and storage events. Each card of
 * the dashboard is a view in this folder; views reach each other through the
 * controller's delegating methods (openSettings, confirm, renderEditor, ...).
 */
import { saveSettings, showToast } from '../app.js';
import { LIMITS, SERVER, STORAGE_KEYS, TIMING } from '../config.js';
import { POST_FILTERS } from '../services/buffer.js';
import { hasContent } from '../store.js';
import { $, formatDuration, icon, pluralize } from '../util.js';
import { Dialogs } from './dialogs.js';
import { byId, hasFiles } from './dom.js';
import { EditorView } from './editor.js';
import { GalleryView } from './gallery.js';
import { ImageEditView } from './imageedit.js';
import { LogView } from './log.js';
import { OperationsView } from './operations.js';
import { Orchestrator } from './orchestrator.js';
import { PostsView } from './posts.js';
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
    this.bindHeader();
    this.upload.bind();
    this.imageEdit.bind();
    this.gallery.bind();
    this.operations.bind();
    this.editor.bind();
    this.posts.bind();
    this.log.bind();
    this.settingsView.bind();
    this.bindGlobal();
    this.bindServices();
    this.renderAll();
    this.showStartupBanners();
    this.feed.load();
  }

  /* ----------------------------------------------- delegates used by views */

  /** @param {string} [sectionId] */
  openSettings(sectionId) { this.settingsView.open(sectionId); }
  confirm(options) { return this.dialogs.confirm(options); }
  choose(options) { return this.dialogs.choose(options); }
  renderEditor() { this.editor.render(); }
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
    byId('plan-interval').value = String(this.prefs.planInterval || this.settings.intervalMinutes);
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
    byId('ai-sub').textContent = `${s.model} · ${demo ? 'Demo mode (simulated)' : 'Live'} · ${s.tone.charAt(0).toUpperCase()}${s.tone.slice(1)} tone`;
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

  renderKpis() {
    const items = this.store.all();
    const generated = items.filter(hasContent).length;
    const ready = items.filter((i) => hasContent(i) && !i.result).length;
    // Upcoming posts in Buffer, counted whenever the posts list includes them ("+" while more pages exist).
    if (this.feed.loaded && this.feed.filter !== 'published') {
      this.upcomingKpi = { count: this.feed.posts.filter((p) => POST_FILTERS.upcoming.includes(p.status)).length, more: Boolean(this.feed.next) };
    }
    byId('kpi-images').textContent = `${items.length} / ${LIMITS.maxImages}`;
    byId('kpi-generated').textContent = String(generated);
    byId('kpi-ready').textContent = String(ready);
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
    });
    this.scheduler.addEventListener('start', (event) => {
      this.lastOp = event.detail;
      this.operations.renderStatus(event.detail);
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
      showToast(`${op.succeeded} of ${pluralize(op.total, noun)} ${isGen ? 'generated' : 'scheduled'}; ${op.failed} failed. Details are in the activity log.`, op.succeeded ? 'warning' : 'error', { title: `${isGen ? 'Generation' : 'Scheduling'} finished with errors` });
      this.notifier.log(op.succeeded ? 'warn' : 'error', isGen ? 'ai' : 'schedule', `${op.label} finished with errors.`, stats);
    } else {
      const verb = isGen ? 'Generated content for' : 'Scheduled';
      showToast(`${verb} ${pluralize(op.succeeded, noun)}${op.skipped ? ` (${op.skipped} skipped)` : ''}${op.retries ? ` after ${pluralize(op.retries, 'retry', 'retries')}` : ''}.`, 'success', { title: isGen ? 'Content ready' : 'Posts scheduled' });
      this.notifier.log('success', isGen ? 'ai' : 'schedule', `${op.label} complete.`, stats);
    }
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
          ? [{ label: 'Switch to Live mode', onClick: () => this.setMode('live'), primary: true }]
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
   * Switches between Demo and Live mode (Live requires valid AI settings).
   * @param {'demo'|'live'} mode
   */
  setMode(mode) {
    if (this.scheduler.isRunning) {
      showToast('Wait for the current operation to finish before switching modes.', 'warning');
      return;
    }
    if (mode === this.settings.mode) return;
    if (mode === 'live') {
      const check = this.validation.validateSettings({ ...this.settings, mode: 'live' }, { scope: 'ollama', requireCredentials: true });
      if (!check.valid) {
        showToast(Object.values(check.errors)[0], 'warning', { title: 'Live mode needs server configuration' });
        this.openSettings('settings-ollama');
        return;
      }
    }
    const result = saveSettings({ ...this.settings, mode });
    if (!result.ok) {
      showToast(Object.values(result.errors)[0], 'error', { title: 'Could not switch mode' });
      this.openSettings();
      return;
    }
    for (const id of ['service-ollama', 'service-buffer', 'service-media-host']) this.notifier.clearBanner(id);
    showToast(mode === 'demo' ? 'Demo mode: AI and scheduling are simulated.' : 'Live mode: requests go to Ollama and Buffer.', 'info', { title: `Switched to ${mode === 'demo' ? 'Demo' : 'Live'} mode` });
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
