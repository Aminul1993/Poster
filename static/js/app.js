/**
 * The application container and its public API. The UI calls these functions;
 * main.js also exposes them on window, so they work from the browser console.
 */
import { APP_VERSION, STORAGE_KEYS } from './config.js';
import { SettingsModel } from './settings.js';
import { isReady } from './store.js';
import { downloadFile, isPlainObject } from './util.js';

/** Services, state and the UI, filled in by main.js at start-up. */
export const app = {
  settings: null,
  settingsSource: 'defaults',
  /** @type {import('./storage.js').StorageService} */ storage: null,
  /** @type {import('./notify.js').NotificationService} */ notifier: null,
  /** @type {import('./validation.js').ValidationService} */ validation: null,
  /** @type {import('./api.js').ApiService} */ api: null,
  /** @type {import('./services/ollama.js').OllamaService} */ liveOllama: null,
  /** @type {import('./demo.js').DemoOllamaService} */ demoOllama: null,
  /** @type {import('./services/imagegen.js').ImageGenService} */ liveImageGen: null,
  /** @type {import('./demo.js').DemoImageGenService} */ demoImageGen: null,
  /** @type {import('./services/buffer.js').BufferService} */ liveBuffer: null,
  /** @type {import('./demo.js').DemoBufferService} */ demoBuffer: null,
  /** @type {import('./store.js').ImageStore} */ store: null,
  /** @type {import('./scheduler.js').SchedulerService} */ scheduler: null,
  /** @type {import('./feed.js').BufferPostsFeed} */ feed: null,
  /** @type {import('./ui/controller.js').UIController} */ ui: null,
  /** @returns {import('./services/ollama.js').OllamaService} the service for the current mode */
  getOllama() { return this.settings.mode === 'demo' ? this.demoOllama : this.liveOllama; },
  /** @returns {import('./services/imagegen.js').ImageGenService} the service for the current mode */
  getImageGen() { return this.settings.mode === 'demo' ? this.demoImageGen : this.liveImageGen; },
  /** @returns {import('./services/buffer.js').BufferService} the service for the current mode */
  getBuffer() { return this.settings.mode === 'demo' ? this.demoBuffer : this.liveBuffer; },
  /** @param {'demo'|'live'} mode @returns {import('./services/buffer.js').BufferService} */
  bufferFor(mode) { return mode === 'demo' ? this.demoBuffer : this.liveBuffer; }
};

/**
 * Generates content for one image (defaults to the one open in the editor).
 * @param {string} [imageId] @returns {Promise<import('./scheduler.js').Operation|null>}
 */
export function generateContentForImage(imageId = app.store.activeId) {
  return app.ui.startGeneration(imageId ? [imageId] : [], 'single');
}

/** Generates content for every selected image. @returns {Promise<import('./scheduler.js').Operation|null>} */
export function generateForSelectedImages() {
  return app.ui.startGeneration(app.store.selected().map((i) => i.id), 'selected');
}

/** Generates content for every image in the gallery. @returns {Promise<import('./scheduler.js').Operation|null>} */
export function generateForAllImages() {
  return app.ui.startGeneration(app.store.all().map((i) => i.id), 'all');
}

/**
 * Schedules one post (defaults to the image open in the editor).
 * @param {string} [imageId] @returns {Promise<import('./scheduler.js').Operation|null>}
 */
export function scheduleSinglePost(imageId = app.store.activeId) {
  return app.ui.startScheduling(imageId ? [imageId] : [], 'single');
}

/** Schedules every selected post. @returns {Promise<import('./scheduler.js').Operation|null>} */
export function scheduleSelectedPosts() {
  return app.ui.startScheduling(app.store.selected().map((i) => i.id), 'selected');
}

/** Schedules every post that has a caption and a publish time and is not yet scheduled. @returns {Promise<import('./scheduler.js').Operation|null>} */
export function scheduleAllPosts() {
  return app.ui.startScheduling(app.store.all().filter((i) => isReady(i, app.settings)).map((i) => i.id), 'all');
}

/**
 * Cancels the running generation or scheduling operation.
 * @returns {boolean} false when nothing was running
 */
export function cancelScheduledOperation() {
  return app.scheduler.cancel();
}

/**
 * Validates an image file against type, size, gallery capacity and duplicates.
 * @param {File} file
 * @param {{fingerprints?: Set<string>, count?: number}} [context] defaults to the current gallery
 * @returns {Promise<{valid: boolean, errors: string[], fingerprint?: string}>}
 */
export function validateImage(file, context = { fingerprints: app.store.fingerprints(), count: app.store.count }) {
  return app.validation.validateImage(file, context);
}

/**
 * Validates settings (defaults to the current ones).
 * @param {object} [settings] @param {object} [options] see ValidationService#validateSettings
 */
export function validateSettings(settings = app.settings, options = {}) {
  return app.validation.validateSettings(settings, options);
}

/**
 * Has the server validate its Buffer token and load the channels, then caches
 * the result in this browser for the channel picker.
 * @param {{mode?: 'demo'|'live', signal?: AbortSignal, onRetry?: Function}} [options]
 * @returns {Promise<{account: object, channels: object[]}>}
 */
export async function connectToBuffer(options = {}) {
  const service = app.bufferFor(options.mode || app.settings.mode);
  const connection = await service.connectToBuffer(options);
  service.saveConnection(connection);
  return connection;
}

/**
 * Checks the Buffer personal access token configured on the server.
 * @param {{signal?: AbortSignal, onRetry?: Function}} [options]
 */
export function validateBufferToken(options = {}) {
  return app.getBuffer().validateBufferToken(options);
}

/**
 * Resolves (uploading if needed) the public URL for an image item, and the
 * stored thumbnail's URL when this call uploaded it.
 * @param {object} item @param {object} [options]
 * @returns {Promise<{url: string|null, thumbnailUrl: string|null}>}
 */
export function uploadMedia(item, options = {}) {
  return app.getBuffer().uploadMedia(item, options);
}

/**
 * Creates a scheduled post in Buffer for each channel.
 * @param {{text: string, channelIds: string[], dueAt: Date, mediaUrl?: string|null}} post
 * @param {object} [options]
 */
export function schedulePost(post, options = {}) {
  return app.getBuffer().schedulePost(post, options);
}

/**
 * Validates, persists and applies settings.
 * @param {object} nextSettings
 * @returns {{ok: boolean, errors?: Object<string,string>, persisted?: boolean, settings?: object, warnings?: string[]}}
 */
export function saveSettings(nextSettings) {
  const normalized = SettingsModel.normalize(nextSettings);
  const result = app.validation.validateSettings(normalized);
  if (!result.valid) return { ok: false, errors: result.errors };
  const previous = app.settings;
  const persisted = app.storage.set(STORAGE_KEYS.settings, normalized);
  app.settings = normalized;
  app.settingsSource = 'storage';
  app.ui?.onSettingsChanged(previous, normalized);
  return { ok: true, persisted, settings: normalized, warnings: result.warnings };
}

/**
 * Loads preferences: values saved in this browser merged over the server
 * defaults. Falls back to the defaults if nothing (or nothing readable) is saved.
 * @returns {object}
 */
export function loadSettings() {
  const defaults = SettingsModel.defaults();
  const stored = app.storage.get(STORAGE_KEYS.settings, null);
  if (stored === null) {
    app.settingsSource = 'defaults';
    return defaults;
  }
  if (!isPlainObject(stored)) {
    app.settingsSource = 'defaults';
    app.notifier.log('warn', 'settings', 'Saved settings were unreadable; using the server defaults.');
    return defaults;
  }
  app.settingsSource = 'storage';
  return SettingsModel.normalize({ ...defaults, ...stored, channelIds: { ...defaults.channelIds, ...(isPlainObject(stored.channelIds) ? stored.channelIds : {}) } });
}

/**
 * Downloads the browser preferences as JSON. Credentials and endpoints live
 * on the server, so they are never part of an export.
 * @param {object} [settings]
 * @returns {object} the exported document
 */
export function exportConfig(settings = app.settings) {
  const payload = { app: 'Poster', version: APP_VERSION, exportedAt: new Date().toISOString(), settings: SettingsModel.normalize(settings) };
  downloadFile(`poster-settings-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(payload, null, 2));
  app.notifier.log('info', 'settings', 'Exported settings (credentials stay on the server).');
  return payload;
}

/**
 * Imports settings from a JSON file after validating it.
 * @param {File} file
 * @returns {Promise<{ok: boolean, errors: string[], settings?: object, ignored: string[]}>}
 */
export async function importConfig(file) {
  if (!(file instanceof Blob)) return { ok: false, errors: ['Choose a JSON file to import.'], ignored: [] };
  if (file.size > 1024 * 1024) return { ok: false, errors: ['The file is larger than 1 MB, so it is not a Poster settings file.'], ignored: [] };
  let json;
  try {
    json = JSON.parse(await file.text());
  } catch (error) {
    app.notifier.log('error', 'settings', `Import failed: "${file.name}" is not valid JSON.`, error.message);
    return { ok: false, errors: [`The file is not valid JSON (${error.message}).`], ignored: [] };
  }
  const check = app.validation.validateImportPayload(json, app.settings);
  if (!check.valid) {
    app.notifier.log('error', 'settings', `Import of "${file.name}" rejected.`, check.errors.join(' '));
    return { ok: false, errors: check.errors, ignored: check.ignored };
  }
  const result = saveSettings(check.settings);
  if (!result.ok) return { ok: false, errors: Object.values(result.errors), ignored: check.ignored };
  app.notifier.log('success', 'settings', `Imported settings from "${file.name}".`, check.ignored.length ? `Ignored unknown keys: ${check.ignored.join(', ')}` : '');
  return { ok: true, errors: [], settings: result.settings, ignored: check.ignored };
}

/**
 * Shows a toast notification.
 * @param {string} message
 * @param {'info'|'success'|'warning'|'error'} [type]
 * @param {{title?: string, timeout?: number}} [options]
 * @returns {HTMLElement}
 */
export function showToast(message, type = 'info', options = {}) {
  return app.notifier.toast(message, type, options);
}
