/** The Add images card's Edit with AI panel: a photo and a description of a change in, an updated copy in the gallery out. */
import { showToast } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { isCancelled } from '../errors.js';
import { decodeImage, drawScaled } from '../image.js';
import { el, formatBytes, formatDuration, truncate } from '../util.js';
import { byId, hasFiles } from './dom.js';
import { View } from './view.js';

export class ImageEditView extends View {
  constructor(ui) {
    super(ui);
    /** @type {File|null} the photo to change; it is never changed itself, nor added to the gallery */
    this.source = null;
    /** @type {AbortController|null} set while an updated image is being generated */
    this.controller = null;
    this.dragDepth = 0;
  }

  bind() {
    const input = byId('imgedit-input');
    const browse = () => input.click();
    const dropzone = byId('imgedit-dropzone');
    dropzone.addEventListener('click', browse);
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); browse(); }
    });
    byId('imgedit-change').addEventListener('click', browse);
    byId('imgedit-remove').addEventListener('click', () => this.clearSource());
    input.addEventListener('change', () => {
      const [file] = input.files || [];
      input.value = '';
      if (file) this.setSource(file);
    });

    const drop = byId('imgedit-drop');
    drop.addEventListener('dragenter', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      this.dragDepth += 1;
      drop.classList.add('is-dragover');
    });
    drop.addEventListener('dragover', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
    });
    drop.addEventListener('dragleave', () => {
      this.dragDepth = Math.max(0, this.dragDepth - 1);
      if (!this.dragDepth) drop.classList.remove('is-dragover');
    });
    drop.addEventListener('drop', (event) => {
      if (!hasFiles(event)) return;
      // Handled here, so the page-level drop handler does not add the photo to the gallery.
      event.preventDefault();
      this.dragDepth = 0;
      drop.classList.remove('is-dragover');
      const [file] = event.dataTransfer.files || [];
      if (file) this.setSource(file);
    });
    // While this panel is open, a pasted image becomes the photo to change (UploadView leaves it alone).
    document.addEventListener('paste', (event) => {
      if (this.ui.prefs.imageSource !== 'edit') return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, [contenteditable="true"], dialog[open]')) return;
      const file = Array.from(event.clipboardData?.files || []).find((f) => f.type.startsWith('image/'));
      if (!file) return;
      event.preventDefault();
      this.setSource(file);
    });

    const prompt = byId('imgedit-prompt');
    prompt.addEventListener('input', () => {
      this.render();
      if (prompt.getAttribute('aria-invalid') === 'true') this.showError('');
    });
    prompt.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        this.editImage();
      }
    });
    byId('imgedit-button').addEventListener('click', () => this.editImage());
    byId('imgedit-cancel').addEventListener('click', () => this.controller?.abort());
  }

  /**
   * Checks a file and shows it as the photo to change.
   * @param {File} file
   */
  async setSource(file) {
    const result = await this.validation.validateImage(file);
    if (!result.valid) {
      this.showError(`"${truncate(file.name || 'image', 60)}" cannot be edited: ${result.errors.join(' ')}`);
      return;
    }
    let preview;
    let size;
    try {
      const image = await decodeImage(file);
      try {
        size = `${image.naturalWidth || image.width}×${image.naturalHeight || image.height}`;
        preview = drawScaled(image, LIMITS.previewMaxEdge).toDataURL('image/jpeg', 0.85);
      } finally {
        if (typeof image.close === 'function') image.close();
      }
    } catch (error) {
      this.showError(`"${truncate(file.name || 'image', 60)}" cannot be edited: ${error.message}`);
      return;
    }
    this.source = file;
    byId('imgedit-thumb').replaceChildren(el('img', { src: preview, alt: 'The photo to change' }));
    byId('imgedit-source-name').textContent = file.name || 'Pasted image';
    byId('imgedit-source-detail').textContent = `${size} · ${formatBytes(file.size)}`;
    this.showError('');
    this.render();
    byId('imgedit-prompt').focus();
  }

  clearSource() {
    this.source = null;
    byId('imgedit-thumb').replaceChildren();
    this.render();
    byId('imgedit-dropzone').focus();
  }

  /** The photo, counter, button and status line. The panel itself is shown by UploadView.renderGenerator(). */
  render() {
    const generating = this.controller !== null;
    const demo = this.settings.mode === 'demo';
    const full = this.store.count >= LIMITS.maxImages;
    const service = this.app.getImageGen();
    byId('imgedit-dropzone').hidden = Boolean(this.source);
    byId('imgedit-source').hidden = !this.source;

    const length = byId('imgedit-prompt').value.trim().length;
    const counter = byId('imgedit-count');
    counter.textContent = `${length} / ${LIMITS.maxPromptChars}`;
    counter.classList.toggle('is-over', length > LIMITS.maxPromptChars);

    const button = byId('imgedit-button');
    button.disabled = generating || full || !service.available;
    button.classList.toggle('is-loading', generating);
    byId('imgedit-button-label').textContent = generating ? 'Generating…' : 'Generate updated image';
    byId('imgedit-cancel').hidden = !generating;

    const status = byId('imgedit-status');
    status.classList.toggle('is-warning', !generating && (full || !service.available));
    if (generating) status.textContent = 'Changing your image. This usually takes 10–40 seconds.';
    else if (!service.available) status.textContent = `Image editing is not set up on the server: ${SERVER.imageGen.problem} Get a free key at enter.pollinations.ai, add it to the .env file next to app.py and restart Poster.`;
    else if (full) status.textContent = `The gallery is full (${LIMITS.maxImages} images). Remove an image to add an edited one.`;
    else if (demo) status.textContent = 'Demo mode tints the photo and writes your description on it instead of calling the image AI.';
    else status.textContent = `Pollinations (${SERVER.imageGen.editModel || 'kontext'}) makes an updated copy and adds it to the gallery; your photo stays as it is. Each run makes a new image.`;
  }

  /** @param {string} message empty hides the error @param {{invalid?: boolean}} [options] invalid marks the description field */
  showError(message, { invalid = false } = {}) {
    const node = byId('imgedit-error');
    node.textContent = message;
    node.hidden = !message;
    byId('imgedit-prompt').setAttribute('aria-invalid', String(Boolean(message) && invalid));
  }

  /** Makes an updated copy of the photo as described and adds it to the gallery, like an upload. */
  async editImage() {
    if (this.controller) return;
    const input = byId('imgedit-prompt');
    const prompt = input.value.replace(/\s+/g, ' ').trim();
    if (!this.source) {
      this.showError('Add the photo to change first.');
      byId('imgedit-dropzone').focus();
      return;
    }
    let problem = '';
    if (prompt.length < LIMITS.minPromptChars) problem = 'Describe the change in a few words first.';
    else if (prompt.length > LIMITS.maxPromptChars) problem = `Shorten the description to ${LIMITS.maxPromptChars} characters or fewer.`;
    this.showError(problem, { invalid: true });
    if (problem) {
      input.focus();
      return;
    }
    if (this.store.count >= LIMITS.maxImages) {
      showToast(`The gallery is full (${LIMITS.maxImages} images). Remove an image to add another.`, 'warning');
      return;
    }

    const service = this.app.getImageGen();
    const source = this.source;
    const controller = new AbortController();
    this.controller = controller;
    this.render();
    const started = Date.now();
    this.notifier.log('info', 'image', `Editing "${truncate(source.name || 'image', 60)}" with AI${service.isDemo ? ' (demo)' : ''}: "${truncate(prompt, 80)}"`);
    try {
      const file = await service.editImage(source, prompt, {
        signal: controller.signal,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Image edit: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      });
      this.notifier.log('success', 'image', `Generated "${file.name}" (${formatBytes(file.size)}) from "${truncate(source.name || 'image', 60)}" in ${formatDuration(Date.now() - started)}.`);
      await this.ui.upload.addGeneratedImage(file);
    } catch (error) {
      if (isCancelled(error)) {
        this.notifier.log('info', 'image', 'Image edit cancelled.');
      } else {
        this.showError(error.hint ? `${error.message} ${error.hint}` : error.message);
        this.notifier.log('error', 'image', `Image edit failed: ${error.message}`, error.hint || '');
        showToast(error.message, 'error', { title: 'Image not edited' });
      }
    } finally {
      this.controller = null;
      this.render();
    }
  }
}
