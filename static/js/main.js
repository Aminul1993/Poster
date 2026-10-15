/**
 * Poster — AI social media publishing (browser side of the Flask app).
 *
 * The Flask server holds the credentials and talks to Ollama, Buffer and the
 * media host. This code only calls the server's /api/* routes, so no API key
 * ever reaches the browser and CORS rules of those services do not apply.
 *
 * Modules (plain ES2020+, no dependencies):
 *   config, util, errors, time, settings  configuration and helpers
 *   storage, notify, validation, api       localStorage, notifications, checks, fetch
 *   services/ollama, imagegen, buffer      the Poster server's /api routes
 *   demo                                   simulated services for Demo mode
 *   store, scheduler, feed                 gallery state, bulk runs, the posts list
 *   ui/controller and ui/*                 one view per dashboard card
 *   app                                    the container and the public API
 */
import * as api from './app.js';
import { app, loadSettings, showToast } from './app.js';
import { APP_VERSION } from './config.js';
import { ApiService } from './api.js';
import { DemoBufferService, DemoImageGenService, DemoOllamaService } from './demo.js';
import { BufferPostsFeed } from './feed.js';
import { NotificationService } from './notify.js';
import { SchedulerService } from './scheduler.js';
import { BufferService } from './services/buffer.js';
import { ImageGenService } from './services/imagegen.js';
import { OllamaService } from './services/ollama.js';
import { StorageService } from './storage.js';
import { ImageStore } from './store.js';
import { UIController } from './ui/controller.js';
import { ValidationService } from './validation.js';

/** The public API, as it was before modules: callable from the browser console. */
const PUBLIC_API = [
  'generateContentForImage', 'generateForSelectedImages', 'generateForAllImages', 'scheduleSinglePost', 'scheduleSelectedPosts',
  'scheduleAllPosts', 'cancelScheduledOperation', 'validateImage', 'validateSettings', 'connectToBuffer', 'validateBufferToken',
  'uploadMedia', 'schedulePost', 'saveSettings', 'loadSettings', 'exportConfig', 'importConfig', 'showToast'
];

function bootstrap() {
  app.storage = new StorageService('poster');
  app.notifier = new NotificationService(app.storage);
  app.validation = new ValidationService(() => app.settings);
  app.settings = loadSettings();
  app.api = new ApiService();
  const deps = { api: app.api, validation: app.validation, notifier: app.notifier, storage: app.storage, getSettings: () => app.settings };
  app.liveOllama = new OllamaService(deps);
  app.demoOllama = new DemoOllamaService(deps);
  app.liveImageGen = new ImageGenService(deps);
  app.demoImageGen = new DemoImageGenService(deps);
  app.liveBuffer = new BufferService(deps);
  app.demoBuffer = new DemoBufferService(deps);
  app.store = new ImageStore({ storage: app.storage });
  app.scheduler = new SchedulerService({
    storage: app.storage,
    notifier: app.notifier,
    validation: app.validation,
    store: app.store,
    getSettings: () => app.settings,
    getOllama: () => app.getOllama(),
    getBuffer: () => app.getBuffer()
  });
  app.feed = new BufferPostsFeed({ notifier: app.notifier, store: app.store, getBuffer: () => app.getBuffer() });
  app.ui = new UIController(app);
  app.ui.init();
  app.notifier.log('info', 'system', `Poster ${APP_VERSION} started in ${app.settings.mode === 'demo' ? 'Demo' : 'Live'} mode using ${app.settingsSource === 'storage' ? 'saved settings' : 'the server defaults'}.`);

  window.addEventListener('error', (event) => {
    const message = event.error?.message || event.message || 'Unknown error';
    app.notifier.log('error', 'system', `Unexpected error: ${message}`);
    showToast(`Something went wrong: ${message}`, 'error', { title: 'Unexpected error' });
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const message = reason?.message || String(reason);
    app.notifier.log('error', 'system', `Unexpected error: ${message}`, reason?.hint || '');
    showToast(message, 'error', { title: 'Unexpected error' });
  });
}

for (const name of PUBLIC_API) window[name] = api[name];
window.poster = app;

try {
  bootstrap();
} catch (error) {
  console.error(error);
  const main = document.getElementById('main');
  const box = document.createElement('div');
  box.className = 'banner banner-error';
  box.setAttribute('role', 'alert');
  box.textContent = `Poster could not start: ${error && error.message ? error.message : error}. Try "Reset All Settings" by clearing this site's data, or check the browser console.`;
  if (main) main.prepend(box); else document.body.prepend(box);
}
