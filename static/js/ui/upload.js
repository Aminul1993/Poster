/** The Add images card: drag and drop, browse, paste, and Generate with AI (Edit with AI is ImageEditView). */
import { showToast, validateImage } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { isCancelled } from '../errors.js';
import { el, formatBytes, formatDuration, pluralize, truncate } from '../util.js';
import { byId, hasFiles } from './dom.js';
import { View } from './view.js';

export class UploadView extends View {
  constructor(ui) {
    super(ui);
    this.dragDepth = 0;
    this.uploading = false;
    /** @type {Promise<void>} settles when the batch being checked is done */
    this.idle = Promise.resolve();
    /** @type {AbortController|null} set while an image is being generated */
    this.imageGenController = null;
  }

  bind() {
    this.bindUpload();
    this.bindGenerator();
  }

  bindUpload() {
    const dropzone = byId('dropzone');
    const input = byId('file-input');
    const openPicker = () => {
      if (this.store.count >= LIMITS.maxImages) {
        showToast(`The gallery is full (${LIMITS.maxImages} images). Remove an image to add another.`, 'warning');
        return;
      }
      input.click();
    };
    dropzone.addEventListener('click', openPicker);
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openPicker(); }
    });
    input.addEventListener('change', () => {
      const files = Array.from(input.files || []);
      input.value = '';
      this.handleFiles(files);
    });
    dropzone.addEventListener('dragenter', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.dragDepth += 1;
      dropzone.classList.add('is-dragover');
    });
    dropzone.addEventListener('dragover', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
    });
    dropzone.addEventListener('dragleave', () => {
      this.dragDepth = Math.max(0, this.dragDepth - 1);
      if (!this.dragDepth) dropzone.classList.remove('is-dragover');
    });
    dropzone.addEventListener('drop', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.dragDepth = 0;
      dropzone.classList.remove('is-dragover');
      this.handleFiles(Array.from(event.dataTransfer.files || []));
    });
    byId('dismiss-upload-errors').addEventListener('click', () => { byId('upload-errors').hidden = true; });
    document.addEventListener('paste', (event) => {
      if (this.ui.prefs.imageSource === 'edit') return; // ImageEditView takes it as the photo to change
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, [contenteditable="true"], dialog[open]')) return;
      const files = Array.from(event.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'));
      if (!files.length) return;
      event.preventDefault();
      this.handleFiles(files);
    });
  }

  /**
   * Validates and adds files to the gallery, reporting every rejection.
   * @param {File[]} files
   * @returns {Promise<object[]>} the items that were added
   */
  async handleFiles(files) {
    if (!files.length) return [];
    if (this.uploading) {
      showToast('Still processing the previous files. Try again in a moment.', 'warning');
      return [];
    }
    this.uploading = true;
    let finished = () => {};
    this.idle = new Promise((resolve) => { finished = resolve; });
    const busy = byId('upload-busy');
    busy.hidden = false;
    const errors = [];
    const accepted = [];
    const fingerprints = this.store.fingerprints();
    let count = this.store.count;
    try {
      for (const [index, file] of files.entries()) {
        byId('upload-busy-label').textContent = `Checking ${index + 1} of ${files.length}: ${truncate(file.name, 50)}`;
        try {
          const result = await validateImage(file, { fingerprints, count });
          if (!result.valid) {
            errors.push({ name: file.name, message: result.errors.join(' ') });
            continue;
          }
          const item = await this.store.createItem(file, result.fingerprint);
          accepted.push(item);
          fingerprints.add(result.fingerprint);
          count += 1;
        } catch (error) {
          errors.push({ name: file.name, message: error?.message || 'The file could not be read.' });
        }
      }
    } finally {
      this.uploading = false;
      busy.hidden = true;
      finished();
    }

    if (accepted.length) {
      this.store.add(accepted);
      const restored = accepted.filter((i) => i.restored);
      this.notifier.log('success', 'upload', `Added ${pluralize(accepted.length, 'image')} to the gallery.`, accepted.map((i) => `${i.name} (${formatBytes(i.size)})`).join(', '));
      if (restored.length) this.notifier.log('info', 'upload', `Restored saved content for ${pluralize(restored.length, 'image')}.`, restored.map((i) => i.name).join(', '));
      showToast(`${pluralize(accepted.length, 'image')} added${restored.length ? ` · saved content restored for ${restored.length}` : ''}.`, 'success');
    }
    this.showUploadErrors(errors, files.length);
    if (errors.length) {
      for (const e of errors) this.notifier.log('warn', 'upload', `Rejected "${e.name}": ${e.message}`);
      showToast(`${pluralize(errors.length, 'file')} could not be added. See the details under the upload area.`, accepted.length ? 'warning' : 'error');
    }
    return accepted;
  }

  showUploadErrors(errors, total) {
    const box = byId('upload-errors');
    if (!errors.length) { box.hidden = true; return; }
    byId('upload-errors-title').textContent = `${errors.length} of ${pluralize(total, 'file')} not added`;
    byId('upload-error-list').replaceChildren(...errors.map((e) => el('li', {}, [el('strong', { text: e.name }), ` — ${e.message}`])));
    box.hidden = false;
  }

  /* ------------------------------------------------------- image generator */

  bindGenerator() {
    for (const radio of document.querySelectorAll('input[name="image-source"]')) {
      radio.addEventListener('change', () => {
        if (!radio.checked) return;
        this.ui.prefs.imageSource = radio.value;
        this.ui.saveUiPrefs();
        this.renderGenerator();
      });
    }
    const prompt = byId('imggen-prompt');
    prompt.addEventListener('input', () => {
      this.renderGenerator();
      if (prompt.getAttribute('aria-invalid') === 'true') this.showGeneratorError('');
    });
    prompt.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        this.generateImage();
      }
    });
    byId('imggen-aspect').addEventListener('change', (event) => {
      this.ui.prefs.imageAspect = event.target.value;
      this.ui.saveUiPrefs();
    });
    byId('imggen-button').addEventListener('click', () => this.generateImage());
    byId('imggen-cancel').addEventListener('click', () => this.imageGenController?.abort());
  }

  /**
   * Adds an image made with AI to the gallery, like an upload, and selects it.
   * @param {File} file @returns {Promise<object|undefined>} the gallery item, unless it was rejected
   */
  async addGeneratedImage(file) {
    // handleFiles() takes one batch at a time; wait for an upload that is still being checked.
    while (this.uploading) await this.idle;
    const [item] = await this.handleFiles([file]);
    if (item) this.store.setActive(item.id);
    return item;
  }

  /** Upload / Generate / Edit panels, plus the generator's counter, button and status line. */
  renderGenerator() {
    const generating = this.imageGenController !== null;
    const source = this.ui.prefs.imageSource;
    const demo = this.settings.mode === 'demo';
    const full = this.store.count >= LIMITS.maxImages;
    const service = this.app.getImageGen();
    byId('source-upload').hidden = source !== 'upload';
    byId('source-generate').hidden = source !== 'generate';
    byId('source-edit').hidden = source !== 'edit';
    const simulated = demo ? ' (simulated in Demo mode)' : '';
    byId('upload-sub').textContent = {
      upload: `JPG, JPEG, PNG or WEBP · up to 20 MB each · maximum ${LIMITS.maxImages} images`,
      generate: `Describe a picture and AI creates it${simulated} · maximum ${LIMITS.maxImages} images`,
      edit: `Add a photo, describe a change and AI makes an updated copy${simulated} · maximum ${LIMITS.maxImages} images`
    }[source];

    const length = byId('imggen-prompt').value.trim().length;
    const counter = byId('imggen-count');
    counter.textContent = `${length} / ${LIMITS.maxPromptChars}`;
    counter.classList.toggle('is-over', length > LIMITS.maxPromptChars);

    const button = byId('imggen-button');
    button.disabled = generating || full || !service.available;
    button.classList.toggle('is-loading', generating);
    byId('imggen-button-label').textContent = generating ? 'Generating…' : 'Generate image';
    byId('imggen-cancel').hidden = !generating;

    const status = byId('imggen-status');
    status.classList.toggle('is-warning', !generating && (full || !service.available));
    if (generating) status.textContent = 'Creating your image. This usually takes 5–30 seconds.';
    else if (!service.available) status.textContent = `Image generation is not set up on the server: ${SERVER.imageGen.problem} Get a free key at enter.pollinations.ai, add it to the .env file next to app.py and restart Poster.`;
    else if (full) status.textContent = `The gallery is full (${LIMITS.maxImages} images). Remove an image to generate another.`;
    else if (demo) status.textContent = 'Demo mode draws a placeholder instead of calling the image AI.';
    else status.textContent = 'Images are created by Pollinations and added to the gallery like uploads. Each run makes a new image.';
  }

  /** @param {string} message empty hides the error @param {{invalid?: boolean}} [options] invalid marks the description field */
  showGeneratorError(message, { invalid = false } = {}) {
    const node = byId('imggen-error');
    node.textContent = message;
    node.hidden = !message;
    byId('imggen-prompt').setAttribute('aria-invalid', String(Boolean(message) && invalid));
  }

  /** Generates an image from the description and adds it to the gallery, like an upload. */
  async generateImage() {
    if (this.imageGenController) return;
    const input = byId('imggen-prompt');
    const prompt = input.value.replace(/\s+/g, ' ').trim();
    let problem = '';
    if (prompt.length < LIMITS.minPromptChars) problem = 'Describe the image in a few words first.';
    else if (prompt.length > LIMITS.maxPromptChars) problem = `Shorten the description to ${LIMITS.maxPromptChars} characters or fewer.`;
    this.showGeneratorError(problem, { invalid: true });
    if (problem) {
      input.focus();
      return;
    }
    if (this.store.count >= LIMITS.maxImages) {
      showToast(`The gallery is full (${LIMITS.maxImages} images). Remove an image to add another.`, 'warning');
      return;
    }

    const service = this.app.getImageGen();
    const controller = new AbortController();
    this.imageGenController = controller;
    this.renderGenerator();
    const started = Date.now();
    this.notifier.log('info', 'image', `Generating an image${service.isDemo ? ' (demo)' : ''}: "${truncate(prompt, 80)}"`);
    try {
      const file = await service.generateImage(prompt, byId('imggen-aspect').value, {
        signal: controller.signal,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Image generation: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      });
      this.notifier.log('success', 'image', `Generated "${file.name}" (${formatBytes(file.size)}) in ${formatDuration(Date.now() - started)}.`);
      await this.addGeneratedImage(file);
    } catch (error) {
      if (isCancelled(error)) {
        this.notifier.log('info', 'image', 'Image generation cancelled.');
      } else {
        this.showGeneratorError(error.hint ? `${error.message} ${error.hint}` : error.message);
        this.notifier.log('error', 'image', `Image generation failed: ${error.message}`, error.hint || '');
        showToast(error.message, 'error', { title: 'Image not generated' });
      }
    } finally {
      this.imageGenController = null;
      this.renderGenerator();
    }
  }
}
