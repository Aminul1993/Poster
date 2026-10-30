/** The Image gallery card: keyed cards, selection, reordering and removal. */
import { showToast } from '../app.js';
import { LIMITS } from '../config.js';
import { countedResult, emptyContent, hasContent, itemStatus } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { applyScriptLang, el, icon, pluralize, splitPostText, truncate } from '../util.js';
import { byId, hasFiles, statusChip } from './dom.js';
import { View } from './view.js';

/** Width from which the AI panel stays on screen beside the gallery (matches poster.css). */
const SIDE_BY_SIDE = '(min-width: 1181px)';

export class GalleryView extends View {
  constructor(ui) {
    super(ui);
    /** @type {Map<string, HTMLElement>} gallery cards by item id */
    this.cards = new Map();
    this.dragId = null;
    this.dropTarget = null;
  }

  /** Keyed render: cards are reused, so images never reload. */
  render() {
    const gallery = byId('gallery');
    const items = this.store.all();
    const focusKey = document.activeElement?.dataset?.focusKey || null;
    const running = this.scheduler.isRunning;
    const seen = new Set();
    items.forEach((item, index) => {
      let card = this.cards.get(item.id);
      if (!card) {
        card = this.createCard(item);
        this.cards.set(item.id, card);
      }
      this.updateCard(card, item, index, items.length, running);
      seen.add(item.id);
      if (gallery.children[index] !== card) gallery.insertBefore(card, gallery.children[index] || null);
    });
    for (const [id, card] of this.cards) {
      if (!seen.has(id)) { card.remove(); this.cards.delete(id); }
    }
    if (focusKey && document.activeElement?.dataset?.focusKey !== focusKey) {
      gallery.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus();
    }
    gallery.hidden = items.length === 0;
    byId('gallery-empty').hidden = items.length > 0;
    byId('gallery-toolbar').hidden = items.length === 0;
    byId('gallery-hint').hidden = gallery.classList.contains('is-list') || this.ui.operations.stage() === 'done';
    const selected = this.store.selected().length;
    const selectAll = byId('select-all');
    selectAll.checked = items.length > 0 && selected === items.length;
    selectAll.indeterminate = selected > 0 && selected < items.length;
    byId('selection-info').textContent = selected ? `${selected} of ${items.length} selected` : 'None selected';
    byId('bulk-edit').hidden = !selected;
    byId('bulk-edit').disabled = running;
  }

  /** Updates one card in place (used while typing in the editor, when nothing else can change). */
  renderCard(id) {
    const card = this.cards.get(id);
    const item = this.store.get(id);
    if (card && item) this.updateCard(card, item, this.store.indexOf(id), this.store.count, this.scheduler.isRunning);
  }

  /** @param {object} item @returns {HTMLElement} */
  createCard(item) {
    const refs = {};
    // Always the thumbnail made in the browser, also after the image is uploaded.
    refs.img = el('img', { src: item.previewUrl, alt: '', loading: 'lazy', draggable: 'false' });
    refs.media = el('button', { type: 'button', class: 'thumb-media', dataset: { action: 'activate', focusKey: `${item.id}:activate` } }, [refs.img]);
    refs.checkbox = el('input', { type: 'checkbox', dataset: { action: 'select', focusKey: `${item.id}:select` }, 'aria-label': `Select ${item.name}` });
    refs.index = el('span', { class: 'thumb-index', 'aria-hidden': 'true' });
    refs.status = el('span', { class: 'thumb-status' });
    refs.overlay = el('div', { class: 'thumb-overlay', hidden: true }, [el('span', { class: 'spinner spinner-light spinner-lg' })]);
    refs.caption = el('div', { class: 'thumb-caption' });
    refs.meta = el('div', { class: 'thumb-meta' });
    // List view: the post's caption and time, edited in place so a whole batch can be read and fixed in one scroll.
    refs.listCaption = el('textarea', { class: 'textarea list-caption', rows: 2, 'aria-label': `Caption for ${item.name}`, dataset: { field: 'caption', focusKey: `${item.id}:caption` } });
    refs.listTime = el('input', { type: 'datetime-local', class: 'input list-time', 'aria-label': `Publish time for ${item.name}`, dataset: { field: 'time', focusKey: `${item.id}:time` } });
    refs.listMeta = el('span', { class: 'list-meta' });
    refs.listStatus = el('span', { class: 'list-status' });
    refs.listTags = el('div', { class: 'list-tags' });
    refs.listFields = el('div', { class: 'list-fields' }, [refs.listCaption, refs.listTags, el('div', { class: 'list-row' }, [refs.listTime, refs.listStatus, refs.listMeta])]);
    // The arrow buttons stay for the mouse; keyboard users reorder with Alt+←/→ on the card, so they are not tab stops.
    refs.left = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', tabindex: '-1', dataset: { action: 'move-left', focusKey: `${item.id}:left` }, 'aria-label': `Move ${item.name} earlier`, title: 'Move earlier (Alt+←)' }, [icon('arrow-left')]);
    refs.right = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', tabindex: '-1', dataset: { action: 'move-right', focusKey: `${item.id}:right` }, 'aria-label': `Move ${item.name} later`, title: 'Move later (Alt+→)' }, [icon('arrow-right')]);
    refs.remove = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', dataset: { action: 'remove', focusKey: `${item.id}:remove` }, 'aria-label': `Remove ${item.name}`, title: 'Remove image' }, [icon('trash')]);
    const card = el('div', { class: 'thumb-card', role: 'listitem', draggable: 'true', dataset: { id: item.id } }, [
      el('div', { class: 'thumb-media-wrap' }, [
        refs.media,
        el('label', { class: 'thumb-check', title: 'Select' }, [refs.checkbox]),
        refs.index,
        refs.status,
        refs.overlay
      ]),
      el('div', { class: 'thumb-info' }, [
        el('div', { class: 'thumb-name', title: item.name, text: item.name }),
        refs.caption,
        refs.meta,
        refs.listFields
      ]),
      el('div', { class: 'thumb-actions' }, [
        el('span', { class: 'grip', title: 'Drag to reorder', 'aria-hidden': 'true' }, [icon('grip')]),
        refs.left, refs.right, el('span', { class: 'spacer' }), refs.remove
      ])
    ]);
    card._refs = refs;
    return card;
  }

  updateCard(card, item, index, total, running) {
    const refs = card._refs;
    const active = item.id === this.store.activeId;
    const status = itemStatus(item, this.settings);
    this.renderPostSummary(refs, item);
    card.classList.toggle('is-active', active);
    card.classList.toggle('is-selected', item.selected);
    refs.checkbox.checked = item.selected;
    refs.index.textContent = String(index + 1);
    refs.media.setAttribute('aria-label', `Image ${index + 1} of ${total}: ${item.name}, ${status.label}. Open in editor.`);
    refs.media.setAttribute('aria-keyshortcuts', 'Alt+ArrowLeft Alt+ArrowRight');
    // Dragging a row would fight selecting text in its caption, so the List view reorders by keys and arrows only.
    card.draggable = !byId('gallery').classList.contains('is-list');
    this.renderListFields(refs, item);
    refs.media.setAttribute('aria-current', active ? 'true' : 'false');
    if (refs.status.dataset.key !== status.key || refs.status.textContent !== status.label) {
      refs.status.dataset.key = status.key;
      refs.status.replaceChildren(statusChip(status));
      // The List view shows the same chip beside the time, where it has room to be read in full.
      refs.listStatus.replaceChildren(statusChip(status));
    }
    refs.status.title = item.error || status.hint || status.label;
    refs.overlay.hidden = !(item.busy === 'generating' || item.busy === 'scheduling');
    refs.left.disabled = index === 0;
    refs.right.disabled = index === total - 1;
    refs.remove.disabled = running;
  }

  /** The post as it stands, under the photo: the caption's first line, then the publish time and hashtag count. */
  /** The post as it stands: Buffer's text and time once it is scheduled (and maybe edited there), otherwise the draft. */
  postView(item) {
    const result = countedResult(item, this.settings.mode);
    const content = item.content || emptyContent();
    let caption = hasContent(item) ? String(item.useAlternative ? content.alternative_description : content.description) : '';
    let tags = content.hashtags || [];
    if (result?.text) {
      const split = splitPostText(result.text);
      caption = split.caption;
      tags = split.tags ? split.tags.split(' ') : [];
    }
    const due = result?.dueAt ? new Date(result.dueAt)
      : item.scheduleLocal ? TimeZoneUtil.zonedToUtc(item.scheduleLocal, this.settings.timezone)?.date || null : null;
    return { caption, tags, due, locked: Boolean(result) };
  }

  /** The post as it stands, under the photo: the caption (two lines), then the publish time and hashtag count. */
  renderPostSummary(refs, item) {
    const view = this.postView(item);
    const caption = view.caption.trim().replace(/\s+/g, ' ');
    refs.caption.textContent = caption ? truncate(caption, 160) : 'No caption yet';
    refs.caption.title = caption;
    refs.caption.classList.toggle('is-empty', !caption);
    applyScriptLang(refs.caption, caption);
    const when = view.due ? TimeZoneUtil.format(view.due, this.settings.timezone) : 'No publish time';
    refs.meta.textContent = `${when}${view.tags.length ? ` · ${pluralize(view.tags.length, 'tag')}` : ''}`;
  }

  /** The List view's fields, left alone while the owner is typing in them. */
  renderListFields(refs, item) {
    const content = item.content || emptyContent();
    const view = this.postView(item);
    // Editable rows keep the draft exactly as typed; a scheduled row shows the post as Buffer has it.
    const caption = view.locked ? view.caption : (item.useAlternative ? content.alternative_description : content.description);
    const time = view.locked && view.due ? TimeZoneUtil.toLocalInput(view.due, this.settings.timezone) : item.scheduleLocal || '';
    const locked = Boolean(item.busy) || view.locked;
    if (document.activeElement !== refs.listCaption && refs.listCaption.value !== caption) refs.listCaption.value = caption;
    if (document.activeElement !== refs.listTime && refs.listTime.value !== time) refs.listTime.value = time;
    applyScriptLang(refs.listCaption, caption);
    refs.listCaption.placeholder = 'Write a caption, or generate it with AI';
    refs.listCaption.readOnly = locked;
    refs.listTime.readOnly = locked;
    refs.listCaption.title = locked && !item.busy ? 'Scheduled: open the post in the editor to change it' : '';
    // The whole post in the row: its hashtags as read-only pills (edit them in the editor).
    refs.listTags.replaceChildren(...view.tags.map((tag) => {
      const pill = el('span', { class: 'tag tag-static', text: tag });
      applyScriptLang(pill, tag);
      return pill;
    }));
    refs.listMeta.textContent = item.useAlternative ? 'Publishes the alternative caption' : '';
  }

  /** Applies a List view edit, the same way the editor does. @param {string} id @param {'caption'|'time'} field @param {string} value */
  editFromList(id, field, value) {
    const item = this.store.get(id);
    if (!item || item.busy || countedResult(item, this.settings.mode)) return;
    if (field === 'caption') {
      const key = item.useAlternative ? 'alternative_description' : 'description';
      this.store.update(id, { content: { ...(item.content || emptyContent()), [key]: value }, error: null, errorHint: '', errorPhase: null }, { reason: 'list-edit' });
    } else {
      const clears = {
        ...(item.errorPhase === 'schedule' ? { error: null, errorHint: '', errorPhase: null } : {}),
        ...(item.result?.removed && value ? { result: null } : {})
      };
      this.store.update(id, { scheduleLocal: value, showErrors: false, ...clears }, { reason: 'list-edit' });
    }
  }

  /**
   * List view, Alt+↑/↓ in a row's field: the next post opens and the caret moves to the same field in its row.
   * @param {HTMLElement} field @param {number} delta
   */
  focusAdjacent(field, delta) {
    const id = field.closest('.thumb-card')?.dataset.id;
    const next = this.store.all()[this.store.indexOf(id) + delta];
    if (!next) return;
    this.store.setActive(next.id);
    const target = this.cards.get(next.id)?._refs[field.dataset.field === 'caption' ? 'listCaption' : 'listTime'];
    target?.focus();
    target?.scrollIntoView({ block: 'nearest' });
  }

  /** @param {string} id says where a card moved, for anyone who cannot see it move */
  announceMove(id) {
    const item = this.store.get(id);
    if (item) byId('gallery-announce').textContent = `${item.name} moved to position ${this.store.indexOf(id) + 1} of ${this.store.count}.`;
  }

  /** @param {'grid'|'list'} view */
  setView(view) {
    byId('gallery').classList.toggle('is-list', view === 'list');
    document.querySelector(`input[name="gallery-view"][value="${view}"]`).checked = true;
    byId('gallery-hint').hidden = view === 'list';
  }

  /**
   * Brings the editor into view for the post just opened. Beside the gallery the panel is already on screen
   * and only scrolls inside itself; below that width the page scrolls to it.
   * @param {{focus?: boolean}} [options] focus the caption too (keyboard use)
   */
  revealEditor({ focus = false } = {}) {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const sideBySide = window.matchMedia?.(SIDE_BY_SIDE).matches;
    byId('editor').scrollIntoView({ block: sideBySide ? 'nearest' : 'start', behavior: reduce ? 'auto' : 'smooth' });
    if (!focus) return;
    // Focus goes where the next step is: Generate before any caption, "Edit in Buffer" on a locked post, else the caption.
    const caption = byId('ed-description');
    let target = caption;
    if (byId('editor').classList.contains('is-blank')) target = byId('ed-blank-generate');
    else if (byId('editor').classList.contains('is-locked') && !byId('ed-result-edit').hidden) target = byId('ed-result-edit');
    target.focus({ preventScroll: true });
  }

  bind() {
    const gallery = byId('gallery');
    gallery.addEventListener('click', (event) => {
      const target = event.target.closest('[data-action]');
      if (!target || !gallery.contains(target)) return;
      const id = target.closest('.thumb-card')?.dataset.id;
      if (!id) return;
      const item = this.store.get(id);
      switch (target.dataset.action) {
        case 'activate':
          this.store.setActive(id);
          // event.detail is 0 for Enter or Space: a keyboard user goes straight to the caption.
          this.revealEditor({ focus: event.detail === 0 });
          break;
        case 'move-left':
        case 'move-right':
          if (this.store.move(id, target.dataset.action === 'move-left' ? -1 : 1)) {
            this.notifier.log('info', 'user', `Moved "${item.name}" to position ${this.store.indexOf(id) + 1}.`);
          }
          break;
        case 'remove':
          this.removeImage(id);
          break;
        default:
          break;
      }
    });
    gallery.addEventListener('input', (event) => {
      const field = event.target.dataset?.field;
      const id = event.target.closest('.thumb-card')?.dataset.id;
      if (field && id) this.editFromList(id, field, event.target.value);
    });
    document.querySelectorAll('input[name="gallery-view"]').forEach((radio) => radio.addEventListener('change', (event) => {
      if (!event.target.checked) return;
      this.ui.prefs.galleryView = event.target.value;
      this.ui.saveUiPrefs();
      this.setView(event.target.value);
    }));
    gallery.addEventListener('change', (event) => {
      if (!event.target.matches('[data-action="select"]')) return;
      const id = event.target.closest('.thumb-card')?.dataset.id;
      if (id) this.store.setSelected(id, event.target.checked);
    });
    gallery.addEventListener('keydown', (event) => {
      if (!event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') || event.target.dataset?.field) return;
      const id = event.target.closest('.thumb-card')?.dataset.id;
      if (!id) return;
      event.preventDefault();
      if (this.store.move(id, event.key === 'ArrowLeft' ? -1 : 1)) {
        this.notifier.log('info', 'user', `Moved "${this.store.get(id).name}" to position ${this.store.indexOf(id) + 1}.`);
      }
    });
    gallery.addEventListener('dragstart', (event) => {
      const card = event.target.closest?.('.thumb-card');
      if (!card) return;
      this.dragId = card.dataset.id;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', this.dragId);
      card.classList.add('is-dragging');
    });
    gallery.addEventListener('dragover', (event) => {
      if (this.dragId) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        this.clearDropMarkers();
        const card = event.target.closest('.thumb-card');
        if (card && card.dataset.id !== this.dragId) {
          const rect = card.getBoundingClientRect();
          const after = event.clientX > rect.left + rect.width / 2;
          card.classList.add(after ? 'drop-after' : 'drop-before');
          this.dropTarget = { id: card.dataset.id, after };
        } else {
          this.dropTarget = null;
        }
      } else if (hasFiles(event)) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      }
    });
    gallery.addEventListener('drop', (event) => {
      if (this.dragId) {
        event.preventDefault();
        if (this.dropTarget) {
          const id = this.dragId;
          const target = this.store.indexOf(this.dropTarget.id) + (this.dropTarget.after ? 1 : 0);
          if (this.store.moveTo(id, target)) this.notifier.log('info', 'user', `Moved "${this.store.get(id).name}" to position ${this.store.indexOf(id) + 1}.`);
        }
        this.endDrag();
      } else if (hasFiles(event)) {
        event.preventDefault();
        this.ui.handleFiles(Array.from(event.dataTransfer.files || []));
      }
    });
    document.addEventListener('dragend', () => this.endDrag());
    byId('select-all').addEventListener('change', (event) => this.store.selectAll(event.target.checked));
    byId('clear-images').addEventListener('click', () => this.removeAllImages());
  }

  clearDropMarkers() {
    byId('gallery').querySelectorAll('.drop-before, .drop-after').forEach((node) => node.classList.remove('drop-before', 'drop-after'));
  }

  endDrag() {
    this.clearDropMarkers();
    byId('gallery').querySelectorAll('.is-dragging').forEach((node) => node.classList.remove('is-dragging'));
    this.dragId = null;
    this.dropTarget = null;
  }

  /** @param {string} id */
  removeImage(id) {
    const item = this.store.get(id);
    if (!item) return;
    if (this.scheduler.isRunning) {
      showToast('Wait for the current operation to finish before removing images.', 'warning');
      return;
    }
    const removedFrom = this.cards.get(id);
    const keyboard = removedFrom?.contains(document.activeElement);
    const { index } = this.store.remove(id, { keepPreview: true });
    this.notifier.log('info', 'user', `Removed "${item.name}" from the gallery.`);
    // The keyboard stays in the gallery: on the card now in that place, or on the upload area when none is left.
    if (keyboard) {
      const next = this.store.all()[Math.min(index, this.store.count - 1)];
      (next ? this.cards.get(next.id)?._refs.media : byId('dropzone'))?.focus();
    }
    // Undo puts the image back where it was, with its caption, times and status, until the toast goes.
    let restored = false;
    showToast(`Removed "${truncate(item.name, 40)}".${hasContent(item) ? ' Its content is saved and returns if you upload it again.' : ''}`, 'info', {
      timeout: 8000,
      action: { label: 'Undo', onClick: () => { restored = this.undoRemove(item, index); } },
      onClose: () => { if (!restored) URL.revokeObjectURL(item.previewUrl); }
    });
  }

  /** @param {object} item @param {number} index @returns {boolean} whether the image is back in the gallery */
  undoRemove(item, index) {
    if (this.scheduler.isRunning) {
      showToast(`Wait for the current operation to finish, then add "${truncate(item.name, 40)}" again.`, 'warning');
      return false;
    }
    if (this.store.count >= LIMITS.maxImages || this.store.all().some((i) => i.fingerprint === item.fingerprint)) {
      showToast(this.store.count >= LIMITS.maxImages
        ? `The gallery is full, so "${truncate(item.name, 40)}" was not put back.`
        : `"${truncate(item.name, 40)}" is already in the gallery.`, 'info');
      return false;
    }
    this.store.restore(item, index);
    this.notifier.log('info', 'user', `Put "${item.name}" back in the gallery.`);
    this.cards.get(item.id)?._refs.media.focus({ preventScroll: true });
    return true;
  }

  async removeAllImages() {
    const count = this.store.count;
    if (!count || this.scheduler.isRunning) return;
    const ok = await this.ui.confirm({
      title: `Remove all ${pluralize(count, 'image')}?`,
      message: 'The gallery will be emptied. Generated content stays saved as drafts and is restored if you upload the same files again.',
      confirmLabel: 'Remove all',
      danger: true
    });
    if (!ok) return;
    this.store.clear();
    this.notifier.log('info', 'user', `Removed all ${pluralize(count, 'image')} from the gallery.`);
    showToast('Gallery cleared.', 'info');
  }
}
