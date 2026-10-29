/** The Settings drawer: preferences, server status, Buffer channels, import, export and reset. */
import { connectToBuffer, exportConfig, importConfig, saveSettings, showToast } from '../app.js';
import { LIMITS, SERVER, STORAGE_KEYS } from '../config.js';
import { serviceMeta } from '../services/buffer.js';
import { SettingsModel } from '../settings.js';
import { TimeZoneUtil } from '../time.js';
import { charCount, el, formatBytes, icon, pluralize, truncate } from '../util.js';
import { byId, setFieldError } from './dom.js';
import { defaultPrefs } from './prefs.js';
import { View } from './view.js';

/** Settings fields: [settings key, input id, type, label]. Credentials live on the server. */
const SETTINGS_FIELDS = Object.freeze([
  ['model', 'set-model', 'text'],
  ['tone', 'set-tone', 'text'],
  ['aiConcurrency', 'set-ai-concurrency', 'number', 'Parallel requests'],
  ['extraInstructions', 'set-extra', 'text'],
  ['requireMedia', 'set-require-media', 'checkbox'],
  ['timezone', 'set-timezone', 'text'],
  ['minLeadMinutes', 'set-lead', 'number', 'Minimum lead time'],
  ['descMin', 'set-desc-min', 'number', 'Caption minimum length'],
  ['descMax', 'set-desc-max', 'number', 'Caption maximum length']
]);

export class SettingsView extends View {
  constructor(ui) {
    super(ui);
    this.channelDraft = { live: [], demo: [] };
    this.snapshot = '';
  }

  /** Fills the timezone suggestions: typing "Dhaka" or "London" finds the zone among about 400. */
  populateTimezones() {
    const zones = TimeZoneUtil.list();
    const browser = TimeZoneUtil.browserZone();
    byId('timezone-options').replaceChildren(...zones.map((zone) => el('option', { value: zone, label: zone === browser ? 'This device' : undefined })));
    byId('set-timezone').addEventListener('change', (event) => {
      // Accept any spelling the browser knows ("asia/dhaka") and store its canonical name.
      const value = event.target.value.trim();
      if (TimeZoneUtil.isValid(value)) event.target.value = Intl.DateTimeFormat('en-US', { timeZone: value }).resolvedOptions().timeZone;
    });
  }

  bind() {
    const dialog = byId('settings-dialog');
    const form = byId('settings-form');
    form.addEventListener('submit', (event) => { event.preventDefault(); this.saveFromForm(); });
    byId('close-settings').addEventListener('click', () => this.requestClose());
    byId('cancel-settings').addEventListener('click', () => this.requestClose());
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); this.requestClose(); });
    form.addEventListener('input', () => this.updateDirtyState());
    form.addEventListener('change', (event) => {
      if (event.target.name === 'mode') this.renderChannelList();
      this.updateDirtyState();
    });
    for (const [key, id] of SETTINGS_FIELDS) {
      byId(id).addEventListener('blur', () => this.validateField(key, id));
    }
    byId('set-extra').addEventListener('input', () => {
      byId('set-extra-count').textContent = `${charCount(byId('set-extra').value)} / ${LIMITS.maxExtraInstructions}`;
    });
    byId('channel-list').addEventListener('change', (event) => {
      const input = event.target.closest('input[data-channel-id]');
      if (!input) return;
      const mode = this.formMode();
      const set = new Set(this.channelDraft[mode]);
      if (input.checked) set.add(input.dataset.channelId); else set.delete(input.dataset.channelId);
      this.channelDraft[mode] = [...set];
      byId('channel-count').textContent = `${set.size} selected`;
      this.updateDirtyState();
    });
    byId('test-ollama').addEventListener('click', () => this.testOllama());
    byId('connect-buffer').addEventListener('click', () => this.connectBuffer());
    byId('export-config').addEventListener('click', () => {
      const { settings } = this.read();
      settings.channelIds = this.channelDraft;
      exportConfig(settings);
      showToast('Settings exported. Credentials stay on the server and are not included.', 'success');
    });
    byId('import-config').addEventListener('click', () => byId('import-input').click());
    byId('import-input').addEventListener('change', async (event) => {
      const file = event.target.files?.[0];
      event.target.value = '';
      if (!file) return;
      const result = await importConfig(file);
      if (result.ok) {
        this.fill(result.settings);
        showToast(`Settings imported from "${truncate(file.name, 40)}"${result.ignored.length ? ` (${result.ignored.length} unknown keys ignored)` : ''}.`, 'success');
      } else {
        this.showErrors(Object.fromEntries(result.errors.map((message, i) => [`import${i}`, message])), 'The file was not imported');
        showToast(result.errors[0], 'error', { title: 'Import failed' });
      }
    });
    byId('restore-defaults').addEventListener('click', () => {
      this.fill(SettingsModel.defaults(), { keepSnapshot: true });
      this.updateDirtyState();
      showToast('Server defaults loaded into the form. Click "Save settings" to apply them.', 'info');
    });
    byId('reset-all').addEventListener('click', () => this.resetAll());
    byId('clear-drafts').addEventListener('click', async () => {
      const count = this.store.draftCount();
      const ok = await this.ui.confirm({
        title: 'Clear saved drafts?',
        message: `${pluralize(count, 'saved draft')} (generated content, times and URLs remembered per image) will be deleted. Images currently in the gallery keep their content until you remove them.`,
        confirmLabel: 'Clear drafts', danger: true
      });
      if (!ok) return;
      this.store.clearDrafts();
      this.notifier.log('info', 'settings', `Cleared ${pluralize(count, 'saved draft')}.`);
      this.renderStorageUsage();
      showToast('Saved drafts cleared.', 'success');
    });
  }

  /** @returns {'demo'|'live'} the mode selected in the settings form */
  formMode() {
    return document.querySelector('input[name="mode"]:checked')?.value === 'live' ? 'live' : 'demo';
  }

  /** @param {string} [sectionId] section to scroll to */
  open(sectionId) {
    const dialog = byId('settings-dialog');
    if (!dialog.open) {
      this.fill(this.settings);
      byId('ollama-test-result').hidden = true;
      byId('buffer-test-result').hidden = true;
      dialog.showModal();
    }
    if (sectionId) {
      const section = byId(sectionId);
      section?.scrollIntoView({ block: 'start' });
      section?.querySelector('input:not([type="hidden"]), select, textarea')?.focus({ preventScroll: true });
    } else {
      byId('close-settings').focus();
    }
  }

  /**
   * Fills the form from a settings object.
   * @param {object} settings @param {{keepSnapshot?: boolean}} [options]
   */
  fill(settings, { keepSnapshot = false } = {}) {
    const s = SettingsModel.normalize(settings);
    for (const [key, id, type] of SETTINGS_FIELDS) {
      const input = byId(id);
      if (type === 'checkbox') input.checked = Boolean(s[key]);
      else input.value = s[key] ?? '';
    }
    document.querySelector(`input[name="mode"][value="${s.mode}"]`).checked = true;
    this.channelDraft = { live: [...s.channelIds.live], demo: [...s.channelIds.demo] };
    byId('set-extra-count').textContent = `${charCount(s.extraInstructions)} / ${LIMITS.maxExtraInstructions}`;
    byId('settings-source').textContent = this.app.settingsSource === 'storage'
      ? 'Using preferences saved in this browser'
      : 'Using the server defaults (nothing saved in this browser yet)';
    this.showErrors({});
    this.renderServerInfo();
    this.renderChannelList();
    this.renderStorageUsage();
    if (!keepSnapshot) this.snapshot = JSON.stringify(this.read().settings);
    this.updateDirtyState();
  }

  /**
   * Read-only view of what the server has configured. Secrets are never sent
   * to the browser, only whether they are present.
   */
  renderServerInfo() {
    const row = (label, value, state = '') => el('div', {}, [el('dt', { text: label }), el('dd', { class: state ? `is-${state}` : '', text: value })]);
    const note = (problem) => el('div', {}, [el('span', { class: 'server-note', text: `${problem} Set it in the .env file next to app.py, then restart Poster.` })]);
    const status = (service) => row('Status', service.configured ? 'Configured on the server' : 'Not configured', service.configured ? 'ok' : 'missing');
    const o = SERVER.ollama;
    byId('ollama-server-info').replaceChildren(...[
      status(o),
      row('Endpoint', o.host ? `${o.host}${o.style === 'openai' ? ' (OpenAI-compatible)' : ''}` : '—'),
      row('Default model', o.model || '—'),
      o.models?.length ? row('Allowed models', o.models.join(', ')) : null,
      row('Timeout', `${o.timeout} s per request · up to 3 retries`),
      o.configured ? null : note(o.problem)
    ].filter(Boolean));
    const b = SERVER.buffer;
    byId('buffer-server-info').replaceChildren(...[
      status(b),
      row('API', b.host || '—'),
      row('Access token', b.tokenId ? 'Set on the server' : 'Missing', b.tokenId ? 'ok' : 'missing'),
      b.configured ? null : note(b.problem)
    ].filter(Boolean));
    const m = SERVER.media;
    byId('media-server-info').replaceChildren(...[
      row('Status', m.configured ? 'Configured on the server' : 'Not configured: paste public image URLs in the editor', m.configured ? 'ok' : 'missing'),
      m.host ? row('Host', m.host) : null,
      m.configured || !m.host ? null : note(m.problem)
    ].filter(Boolean));
  }

  /**
   * Reads the form. Numeric fields that are empty or not numbers are reported
   * as errors instead of silently falling back to defaults.
   * @returns {{settings: object, errors: Object<string,string>}}
   */
  read() {
    const raw = { mode: this.formMode() };
    const errors = {};
    for (const [key, id, type, label] of SETTINGS_FIELDS) {
      const input = byId(id);
      if (type === 'checkbox') raw[key] = input.checked;
      else if (type === 'number') {
        const value = input.value.trim();
        const number = Number(value);
        if (!value || !Number.isFinite(number)) errors[key] = `${label} must be a number.`;
        raw[key] = Number.isFinite(number) ? number : value;
      } else raw[key] = input.value.trim();
    }
    const settings = SettingsModel.normalize(raw);
    for (const key of Object.keys(errors)) settings[key] = raw[key];
    settings.channelIds = { live: [...this.channelDraft.live], demo: [...this.channelDraft.demo] };
    return { settings, errors };
  }

  updateDirtyState() {
    const dialog = byId('settings-dialog');
    if (!dialog.open) return;
    byId('settings-dirty').textContent = this.isDirty() ? 'Unsaved changes' : '';
  }

  isDirty() {
    return JSON.stringify(this.read().settings) !== this.snapshot;
  }

  async requestClose() {
    if (this.isDirty()) {
      const ok = await this.ui.confirm({ title: 'Discard unsaved changes?', message: 'Your edits to the settings have not been saved.', confirmLabel: 'Discard', danger: true });
      if (!ok) return;
    }
    byId('settings-dialog').close();
  }

  /** Validates one field on blur and shows its message inline. */
  validateField(key, id) {
    const { settings, errors } = this.read();
    const result = this.validation.validateSettings(settings, { requireCredentials: settings.mode === 'live' });
    setFieldError(id, errors[key] || result.errors[key] || null);
  }

  /**
   * Shows field errors inline plus a summary list.
   * @param {Object<string,string>} errors @param {string} [title]
   */
  showErrors(errors, title = 'Please fix the highlighted settings') {
    const fieldIds = Object.fromEntries(SETTINGS_FIELDS.map(([key, id]) => [key, id]));
    for (const [, id] of SETTINGS_FIELDS) setFieldError(id, null);
    for (const [key, message] of Object.entries(errors)) if (fieldIds[key]) setFieldError(fieldIds[key], message);
    const box = byId('settings-errors');
    const messages = Object.values(errors);
    box.hidden = !messages.length;
    box.querySelector('strong').textContent = title;
    byId('settings-error-list').replaceChildren(...messages.map((m) => el('li', { text: m })));
  }

  saveFromForm() {
    const { settings, errors: formErrors } = this.read();
    const check = this.validation.validateSettings(settings);
    const errors = { ...check.errors, ...formErrors };
    if (Object.keys(errors).length) {
      this.showErrors(errors);
      byId('settings-errors').scrollIntoView({ block: 'nearest' });
      const firstKey = Object.keys(errors)[0];
      const field = SETTINGS_FIELDS.find(([key]) => key === firstKey);
      if (field) byId(field[1]).focus({ preventScroll: false });
      showToast(`Fix ${pluralize(Object.keys(errors).length, 'setting')} before saving.`, 'error');
      return;
    }
    const result = saveSettings(settings);
    if (!result.ok) {
      this.showErrors(result.errors);
      return;
    }
    this.snapshot = JSON.stringify(this.read().settings);
    byId('settings-dialog').close();
    this.notifier.log('info', 'settings', `Saved settings (${settings.mode === 'demo' ? 'Demo' : 'Live'} mode, ${settings.model}, ${settings.timezone}).`);
    showToast(result.persisted ? 'Settings saved.' : 'Settings applied for this session only (browser storage unavailable).', result.persisted ? 'success' : 'warning');
    for (const warning of result.warnings) showToast(warning, 'info');
  }

  renderChannelList() {
    const mode = this.formMode();
    const list = byId('channel-list');
    const channels = mode === 'demo' ? SettingsModel.demoChannels : (this.app.liveBuffer.getConnection()?.channels || []);
    const selected = new Set(this.channelDraft[mode]);
    byId('connect-buffer-label').textContent = mode === 'demo' ? 'Reload demo channels' : (channels.length ? 'Reconnect & refresh channels' : 'Connect & load channels');
    if (!channels.length) {
      const savedIds = this.channelDraft.live;
      list.replaceChildren(el('p', { class: 'hint', text: savedIds.length
        ? `${pluralize(savedIds.length, 'channel')} selected from an earlier connection or import. Connect to see their names and status.`
        : (SERVER.buffer.configured ? 'Connect to load the channels of the Buffer account configured on the server.' : 'Buffer is not configured on the server, so there are no channels to load.') }));
      byId('channel-count').textContent = savedIds.length ? `${savedIds.length} selected` : '';
      return;
    }
    list.replaceChildren(...channels.map((channel) => {
      const meta = serviceMeta(channel.service);
      const disabled = !channel.usable;
      const status = channel.isDisconnected ? 'disconnected' : channel.isLocked ? 'locked' : channel.isQueuePaused ? 'queue paused' : '';
      return el('label', { class: `channel${disabled ? ' is-disabled' : ''}` }, [
        el('input', { type: 'checkbox', checked: selected.has(channel.id), disabled, dataset: { channelId: channel.id }, 'aria-label': `${channel.name} on ${meta.label}` }),
        // A touch darker than the brand colour, so the white initial passes 4.5:1 on every network's colour.
        el('span', { class: 'channel-avatar', style: `background:color-mix(in srgb, ${meta.color} 76%, #000)`, 'aria-hidden': 'true', text: meta.label.charAt(0) }),
        el('span', { class: 'channel-info' }, [
          el('span', { class: 'channel-name', text: channel.name, style: 'display:block' }),
          el('span', { class: 'channel-sub', style: 'display:block', text: [meta.label, channel.handle ? `@${channel.handle}` : '', channel.organizationName, status].filter(Boolean).join(' · ') })
        ])
      ]);
    }));
    byId('channel-count').textContent = `${channels.filter((c) => selected.has(c.id)).length} selected`;
  }

  renderStorageUsage() {
    const storage = this.app.storage;
    byId('storage-usage').textContent = storage.persistent
      ? `Using about ${formatBytes(storage.usageBytes())} of browser storage · ${pluralize(this.store.draftCount(), 'saved draft')}.`
      : 'Browser storage is unavailable, so nothing is saved after this tab closes.';
  }

  /**
   * Shows a status line under a settings action.
   * @param {string} id @param {'ok'|'error'|'info'} kind @param {string} text
   */
  showTestResult(id, kind, text) {
    const box = byId(id);
    box.hidden = false;
    box.className = `test-result is-${kind}`;
    box.replaceChildren(icon(kind === 'ok' ? 'check-circle' : kind === 'error' ? 'alert-circle' : 'info'), el('span', { text }));
  }

  /** @param {HTMLButtonElement} button @param {boolean} loading */
  setLoading(button, loading) {
    button.classList.toggle('is-loading', loading);
    button.disabled = loading;
    button.setAttribute('aria-busy', String(loading));
  }

  async testOllama() {
    const { settings } = this.read();
    const button = byId('test-ollama');
    if (this.formMode() === 'demo') {
      this.showTestResult('ollama-test-result', 'info', 'Demo mode uses simulated AI, so no connection is needed. Switch the mode to Live to test your endpoint.');
      return;
    }
    const check = this.validation.validateSettings(settings, { scope: 'ollama', requireCredentials: true });
    if (!check.valid) {
      this.showErrors(check.errors);
      this.showTestResult('ollama-test-result', 'error', Object.values(check.errors)[0]);
      return;
    }
    this.setLoading(button, true);
    this.showTestResult('ollama-test-result', 'info', 'Testing the connection…');
    try {
      const result = await this.app.liveOllama.testConnection({
        settings,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Ollama test: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      });
      if (result.modelAvailable === false) {
        this.showTestResult('ollama-test-result', 'error', `Connected to ${result.host}, but "${settings.model}" is not in its list of ${pluralize(result.models.length, 'model')}. Check the model name.`);
        this.notifier.log('warn', 'settings', `Ollama reachable, but model "${settings.model}" was not listed.`, result.models.slice(0, 20).join(', '));
      } else {
        const detail = result.models.length ? `${pluralize(result.models.length, 'model')} available, including ${settings.model}.` : 'The model list is not available here; the first generation will confirm the model.';
        this.showTestResult('ollama-test-result', 'ok', `Connected to ${result.host}. ${detail}`);
        this.notifier.log('success', 'settings', `Ollama connection test passed (${result.host}).`);
        if (settings.model === this.settings.model) { this.ui.health.ollama = { state: 'ok' }; this.ui.renderHeader(); }
      }
    } catch (error) {
      this.showTestResult('ollama-test-result', 'error', [error.message, error.hint].filter(Boolean).join(' '));
      this.notifier.log('error', 'settings', `Ollama connection test failed: ${error.message}`, error.hint || error.details || '');
    } finally {
      this.setLoading(button, false);
    }
  }

  async connectBuffer() {
    const { settings } = this.read();
    const mode = this.formMode();
    const button = byId('connect-buffer');
    if (mode === 'live') {
      const check = this.validation.validateSettings(settings, { scope: 'buffer', requireCredentials: true });
      if (!check.valid) {
        this.showErrors(check.errors);
        this.showTestResult('buffer-test-result', 'error', Object.values(check.errors)[0]);
        return;
      }
    }
    this.setLoading(button, true);
    this.showTestResult('buffer-test-result', 'info', mode === 'demo' ? 'Loading demo channels…' : 'Verifying the token and loading channels…');
    try {
      const connection = await connectToBuffer({
        mode,
        settings,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Buffer connect: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      });
      const usable = connection.channels.filter((c) => c.usable);
      const known = new Set(connection.channels.map((c) => c.id));
      const kept = this.channelDraft[mode].filter((id) => known.has(id));
      this.channelDraft[mode] = kept.length ? kept : usable.map((c) => c.id);
      const unusable = connection.channels.length - usable.length;
      const summary = mode === 'demo'
        ? `Demo workspace loaded with ${pluralize(connection.channels.length, 'sample channel')}.`
        : `Connected as ${connection.account.email || 'your Buffer account'} · ${pluralize(connection.channels.length, 'channel')} in ${pluralize(connection.account.organizations.length, 'organization')}${unusable ? ` (${unusable} disconnected or locked)` : ''}.`;
      this.showTestResult('buffer-test-result', connection.channels.length ? 'ok' : 'error', connection.channels.length ? summary : 'Connected, but this account has no channels. Connect a social account in Buffer first.');
      this.notifier.log('success', 'settings', mode === 'demo' ? 'Loaded demo channels.' : `Connected to Buffer: ${pluralize(connection.channels.length, 'channel')} found.`);
      if (mode === 'live') {
        this.ui.health.buffer = { state: 'ok' };
        this.ui.renderHeader();
      }
      this.renderChannelList();
      this.updateDirtyState();
    } catch (error) {
      this.showTestResult('buffer-test-result', 'error', [error.message, error.hint].filter(Boolean).join(' '));
      this.notifier.log('error', 'settings', `Buffer connection failed: ${error.message}`, error.hint || error.details || '');
    } finally {
      this.setLoading(button, false);
    }
  }

  async resetAll() {
    const ok = await this.ui.confirm({
      title: 'Reset all settings?',
      message: 'This restores the server defaults and removes your saved preferences, channel selections, the cached Buffer connection, theme and interface preferences.\n\nImages, drafts, scheduled posts and the activity log are kept. Credentials on the server are not affected.',
      confirmLabel: 'Reset all settings',
      danger: true
    });
    if (!ok) return;
    const previous = this.settings;
    for (const key of [STORAGE_KEYS.settings, STORAGE_KEYS.theme, STORAGE_KEYS.ui, STORAGE_KEYS.connection]) this.app.storage.remove(key);
    this.app.settings = SettingsModel.defaults();
    this.app.settingsSource = 'defaults';
    this.ui.health = { ollama: null, buffer: null };
    this.ui.prefs = defaultPrefs();
    this.ui.restoreUiPrefs();
    this.ui.applyTheme(window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light', { persist: false });
    this.fill(this.app.settings);
    this.ui.onSettingsChanged(previous, this.app.settings);
    this.notifier.log('warn', 'settings', 'Reset all settings to the server defaults.');
    showToast('All settings were reset to the server defaults.', 'success');
  }
}
