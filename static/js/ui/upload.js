/** The Add images card: drag and drop, browse, paste, and Generate with AI (Edit with AI is ImageEditView). */
import { showToast, validateImage } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { isCancelled } from '../errors.js';
import { hasContent } from '../store.js';
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
    /** @type {AbortController|null} set while images are being generated */
    this.imageGenController = null;
    /** @type {{done: number, total: number}|null} how far a run of generated images has got */
    this.imageGenProgress = null;
    /** Whether the owner reopened the card after it folded away for review. */
    this.expanded = false;
  }

  bind() {
    this.bindUpload();
    this.bindGenerator();
    byId('upload-toggle').addEventListener('click', () => {
      this.expanded = byId('upload-section').classList.contains('is-collapsed');
      this.renderGenerator();
      if (this.expanded) byId('upload-body').querySelector('.dropzone:not([hidden]), textarea')?.focus();
    });
  }

  /**
   * Once posts have captions the batch is being reviewed, so the card folds to one line ("6 of 10 images")
   * and gives the editor the room. "Add more" opens it again.
   */
  renderCollapse() {
    const items = this.store.all();
    const reviewing = items.some(hasContent);
    if (!reviewing) this.expanded = false;
    const collapsed = reviewing && !this.expanded;
    byId('upload-section').classList.toggle('is-collapsed', collapsed);
    byId('upload-body').hidden = collapsed;
    byId('upload-section').querySelector('.card-header .segmented').hidden = collapsed;
    const toggle = byId('upload-toggle');
    toggle.hidden = !reviewing;
    toggle.setAttribute('aria-expanded', String(!collapsed));
    byId('upload-toggle-label').textContent = collapsed ? 'Add more' : 'Done adding';
    if (collapsed) byId('upload-sub').textContent = `${items.length} of ${LIMITS.maxImages} images in this batch.`;
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
   * @param {{quiet?: boolean}} [options] quiet: no "added" toast (a batch of generated images says it once at the end)
   * @returns {Promise<object[]>} the items that were added
   */
  async handleFiles(files, { quiet = false } = {}) {
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
      if (!quiet) showToast(`${pluralize(accepted.length, 'image')} added${restored.length ? ` · saved content restored for ${restored.length}` : ''}.`, 'success');
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
    // The reasons live in the card body, so a folded card opens to show them.
    this.expanded = true;
    this.renderCollapse();
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
    byId('imggen-number').addEventListener('change', (event) => {
      this.ui.prefs.imageCount = Number(event.target.value) || 1;
      this.ui.saveUiPrefs();
      this.renderGenerator();
    });
    byId('imggen-button').addEventListener('click', () => this.generateImage());
    byId('imggen-cancel').addEventListener('click', () => this.imageGenController?.abort());
  }

  /**
   * Adds an image made with AI to the gallery, like an upload, and selects it.
   * @param {File} file
   * @param {{select?: boolean, quiet?: boolean}} [options] select: open it in the editor; quiet: no "added" toast
   * @returns {Promise<object|undefined>} the gallery item, unless it was rejected
   */
  async addGeneratedImage(file, { select = true, quiet = false } = {}) {
    // handleFiles() takes one batch at a time; wait for an upload that is still being checked.
    while (this.uploading) await this.idle;
    const [item] = await this.handleFiles([file], { quiet });
    if (item && select) this.store.setActive(item.id);
    return item;
  }

  /**
   * @param {number} chosen the number of images the owner picked
   * @returns {number} how many a run makes: the chosen number, but no more than the gallery has room for
   */
  batchSize(chosen) {
    return Math.max(1, Math.min(chosen, LIMITS.maxImages - this.store.count));
  }

  /**
   * A "Number of images" select: numbers the gallery has no room for cannot be chosen, and the chosen number
   * comes back once there is room. Locked while a run is going.
   * @param {HTMLSelectElement} select @param {number} chosen @param {boolean} running
   */
  renderBatchSelect(select, chosen, running) {
    const room = Math.max(1, LIMITS.maxImages - this.store.count);
    for (const option of select.options) option.disabled = Number(option.value) > room;
    if (!running) select.value = String(this.batchSize(chosen));
    select.disabled = running;
  }

  /**
   * Makes `progress.total` images one after another and adds each to the gallery, like an upload, as soon as it
   * is ready; the first opens in the editor. Stops when the gallery is full; a failure or Cancel (an abort
   * error from `make`) ends the run too, with the images made so far kept and counted in `progress.added`.
   * @param {{done: number, total: number, added: number}} progress updated as the run goes
   * @param {(number: number) => Promise<File>} make makes one image; `number` is its place in a batch (0 alone)
   * @param {() => void} onProgress called before each image
   */
  async addImageBatch(progress, make, onProgress) {
    const batch = progress.total > 1;
    for (let index = 0; index < progress.total && this.store.count < LIMITS.maxImages; index += 1) {
      progress.done = index;
      onProgress();
      const started = Date.now();
      const file = await make(batch ? index + 1 : 0);
      this.notifier.log('success', 'image', `Generated "${file.name}" (${formatBytes(file.size)}) in ${formatDuration(Date.now() - started)}.`);
      // The first image of a batch opens in the editor; the rest join the gallery behind it.
      if (await this.addGeneratedImage(file, { select: index === 0, quiet: batch })) progress.added += 1;
    }
    if (batch && progress.added) showToast(`${pluralize(progress.added, 'image')} added.`, 'success');
  }

  /**
   * How a run ended early, for its error message and the log: "Made 2 of 4 images. " once a batch has some.
   * @param {{total: number, added: number}} progress @returns {string}
   */
  static madeSoFar(progress) {
    return progress.total > 1 && progress.added ? `Made ${progress.added} of ${progress.total} images. ` : '';
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

    this.renderBatchSelect(byId('imggen-number'), this.ui.prefs.imageCount, generating);
    const count = this.batchSize(this.ui.prefs.imageCount);
    const progress = this.imageGenProgress;

    const button = byId('imggen-button');
    button.disabled = generating || full || !service.available;
    button.classList.toggle('is-loading', generating);
    byId('imggen-button-label').textContent = generating
      ? (progress && progress.total > 1 ? `Generating ${progress.done + 1} of ${progress.total}…` : 'Generating…')
      : count > 1 ? `Generate ${count} images` : 'Generate image';
    byId('imggen-cancel').hidden = !generating;

    const status = byId('imggen-status');
    status.classList.toggle('is-warning', !generating && (full || !service.available));
    if (generating && progress && progress.total > 1) status.textContent = `Creating image ${progress.done + 1} of ${progress.total}. Each usually takes 5–30 seconds and joins the gallery when it is ready.`;
    else if (generating) status.textContent = 'Creating your image. This usually takes 5–30 seconds.';
    else if (!service.available) status.textContent = `Image generation is not set up on the server: ${SERVER.imageGen.problem} Get a free key at enter.pollinations.ai, add it to the .env file next to app.py and restart Poster.`;
    else if (full) status.textContent = `The gallery is full (${LIMITS.maxImages} images). Remove an image to generate another.`;
    else if (this.ui.prefs.imageCount > count) status.textContent = `The gallery has room for ${pluralize(count, 'more image')}, so ${count === 1 ? 'one is' : `${count} are`} made.`;
    else if (demo) status.textContent = `Demo mode draws ${count > 1 ? 'placeholders' : 'a placeholder'} instead of calling the image AI.`;
    else status.textContent = `Images are created by Pollinations and added to the gallery like uploads. Each one is different${count > 1 ? ', from the same description' : ''}.`;
    this.renderCollapse();
  }

  /** @param {string} message empty hides the error @param {{invalid?: boolean}} [options] invalid marks the description field */
  showGeneratorError(message, { invalid = false } = {}) {
    const node = byId('imggen-error');
    node.textContent = message;
    node.hidden = !message;
    byId('imggen-prompt').setAttribute('aria-invalid', String(Boolean(message) && invalid));
  }

  /**
   * Generates the chosen number of images from the description, one after another, and adds each to the
   * gallery like an upload as soon as it is ready. Cancel or a failure stops the run; the images made so far stay.
   */
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
    const aspect = byId('imggen-aspect').value;
    const progress = { done: 0, total: this.batchSize(this.ui.prefs.imageCount), added: 0 };
    const controller = new AbortController();
    this.imageGenController = controller;
    this.imageGenProgress = progress;
    this.renderGenerator();
    this.notifier.log('info', 'image', `Generating ${progress.total > 1 ? `${progress.total} images` : 'an image'}${service.isDemo ? ' (demo)' : ''}: "${truncate(prompt, 80)}"`);
    try {
      await this.addImageBatch(progress, (number) => service.generateImage(prompt, aspect, {
        signal: controller.signal,
        number,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Image generation: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      }), () => this.renderGenerator());
    } catch (error) {
      const made = UploadView.madeSoFar(progress);
      if (isCancelled(error)) {
        this.notifier.log('info', 'image', `Image generation cancelled.${made ? ` ${made.trim()}` : ''}`);
        if (progress.added) showToast(`${pluralize(progress.added, 'image')} added before you cancelled.`, 'info');
      } else {
        this.showGeneratorError(`${made}${error.message}${error.hint ? ` ${error.hint}` : ''}`);
        this.notifier.log('error', 'image', `Image generation failed: ${made}${error.message}`, error.hint || '');
        showToast(`${made}${error.message}`, 'error', { title: made ? 'Not every image was generated' : 'Image not generated' });
      }
    } finally {
      this.imageGenController = null;
      this.imageGenProgress = null;
      this.renderGenerator();
    }
  }
}
