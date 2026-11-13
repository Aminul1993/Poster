/**
 * The Add images card's Edit with AI panel: a photo and a description of a change in, one or more updated copies
 * in the gallery out.
 */
import { showToast } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { isCancelled } from '../errors.js';
import { decodeImage, drawScaled } from '../image.js';
import { el, formatBytes, pluralize, truncate } from '../util.js';
import { byId, hasFiles } from './dom.js';
import { UploadView } from './upload.js';
import { View } from './view.js';

export class ImageEditView extends View {
  constructor(ui) {
    super(ui);
    /** @type {File|null} the photo to change; it is never changed itself, nor added to the gallery */
    this.source = null;
    /** @type {AbortController|null} set while updated images are being generated */
    this.controller = null;
    /** @type {{done: number, total: number, added: number}|null} how far a run of updated copies has got */
    this.progress = null;
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
    byId('imgedit-number').addEventListener('change', (event) => {
      this.ui.prefs.editCount = Number(event.target.value) || 1;
      this.ui.saveUiPrefs();
      this.render();
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

    const upload = this.ui.upload;
    upload.renderBatchSelect(byId('imgedit-number'), this.ui.prefs.editCount, generating);
    const count = upload.batchSize(this.ui.prefs.editCount);
    const progress = this.progress;
    const batch = progress && progress.total > 1;

    const button = byId('imgedit-button');
    button.disabled = generating || full || !service.canEdit;
    button.classList.toggle('is-loading', generating);
    byId('imgedit-button-label').textContent = generating
      ? (batch ? `Generating ${progress.done + 1} of ${progress.total}…` : 'Generating…')
      : count > 1 ? `Generate ${count} updated images` : 'Generate updated image';
    byId('imgedit-cancel').hidden = !generating;

    const status = byId('imgedit-status');
    status.classList.toggle('is-warning', !generating && (full || !service.canEdit));
    if (generating && batch) status.textContent = `Changing copy ${progress.done + 1} of ${progress.total}. Each usually takes 10–40 seconds and joins the gallery when it is ready.`;
    else if (generating) status.textContent = 'Changing your image. This usually takes 10–40 seconds.';
    else if (!service.available) status.textContent = `Image editing is not set up on the server: ${SERVER.imageGen.problem} Set up an image AI provider in Settings → Credentials & Integrations.`;
    else if (!service.canEdit) status.textContent = `${SERVER.imageGen.label} cannot edit images. Choose another image provider in Settings → Credentials & Integrations.`;
    else if (full) status.textContent = `The gallery is full (${LIMITS.maxImages} images). Remove an image to add an edited one.`;
    else if (this.ui.prefs.editCount > count) status.textContent = `The gallery has room for ${pluralize(count, 'more image')}, so ${count === 1 ? 'one is' : `${count} are`} made.`;
    else if (demo) status.textContent = 'Demo mode tints the photo and writes your description on it instead of calling the image AI.';
    else status.textContent = `${SERVER.imageGen.label}${SERVER.imageGen.editModel ? ` (${SERVER.imageGen.editModel})` : ''} makes ${count > 1 ? `${count} different updated copies` : 'an updated copy'} and adds ${count > 1 ? 'them' : 'it'} to the gallery; your photo stays as it is. Each edit is billed.`;
  }

  /** @param {string} message empty hides the error @param {{invalid?: boolean}} [options] invalid marks the description field */
  showError(message, { invalid = false } = {}) {
    const node = byId('imgedit-error');
    node.textContent = message;
    node.hidden = !message;
    byId('imgedit-prompt').setAttribute('aria-invalid', String(Boolean(message) && invalid));
  }

  /**
   * Makes the chosen number of updated copies of the photo as described, one after another, and adds each to the
   * gallery like an upload as soon as it is ready. Cancel or a failure stops the run; the copies made so far stay.
   */
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
    const progress = { done: 0, total: this.ui.upload.batchSize(this.ui.prefs.editCount), added: 0 };
    const controller = new AbortController();
    this.controller = controller;
    this.progress = progress;
    this.render();
    const copies = progress.total > 1 ? `${progress.total} copies of ` : '';
    this.notifier.log('info', 'image', `Editing ${copies}"${truncate(source.name || 'image', 60)}" with AI${service.isDemo ? ' (demo)' : ''}: "${truncate(prompt, 80)}"`);
    try {
      await this.ui.upload.addImageBatch(progress, (number) => service.editImage(source, prompt, {
        signal: controller.signal,
        number,
        onRetry: ({ attempt, maxRetries, delayMs, error }) => this.notifier.log('warn', 'retry', `Image edit: retry ${attempt}/${maxRetries} in ${delayMs / 1000}s (${error.message})`)
      }), () => this.render());
    } catch (error) {
      const made = UploadView.madeSoFar(progress);
      if (isCancelled(error)) {
        this.notifier.log('info', 'image', `Image edit cancelled.${made ? ` ${made.trim()}` : ''}`);
        if (progress.added) showToast(`${pluralize(progress.added, 'image')} added before you cancelled.`, 'info');
      } else {
        this.showError(`${made}${error.message}${error.hint ? ` ${error.hint}` : ''}`);
        this.notifier.log('error', 'image', `Image edit failed: ${made}${error.message}`, error.hint || '');
        showToast(`${made}${error.message}`, 'error', { title: made ? 'Not every image was edited' : 'Image not edited' });
      }
    } finally {
      this.controller = null;
      this.progress = null;
      this.render();
    }
  }
}
