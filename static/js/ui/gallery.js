/** The Image gallery card: keyed cards, selection, reordering and removal. */
import { showToast } from '../app.js';
import { hasContent, itemStatus } from '../store.js';
import { el, formatBytes, icon, pluralize, truncate } from '../util.js';
import { byId, hasFiles, statusChip } from './dom.js';
import { View } from './view.js';

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
    const selected = this.store.selected().length;
    const selectAll = byId('select-all');
    selectAll.checked = items.length > 0 && selected === items.length;
    selectAll.indeterminate = selected > 0 && selected < items.length;
    byId('selection-info').textContent = selected ? `${selected} of ${items.length} selected` : 'None selected';
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
    refs.left = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', dataset: { action: 'move-left', focusKey: `${item.id}:left` }, 'aria-label': `Move ${item.name} earlier`, title: 'Move earlier (Alt+←)' }, [icon('arrow-left')]);
    refs.right = el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', dataset: { action: 'move-right', focusKey: `${item.id}:right` }, 'aria-label': `Move ${item.name} later`, title: 'Move later (Alt+→)' }, [icon('arrow-right')]);
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
        el('div', { class: 'thumb-meta', text: `${formatBytes(item.size)} · ${item.width}×${item.height}` })
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
    const status = itemStatus(item);
    card.classList.toggle('is-active', active);
    card.classList.toggle('is-selected', item.selected);
    refs.checkbox.checked = item.selected;
    refs.index.textContent = String(index + 1);
    refs.media.setAttribute('aria-label', `Image ${index + 1} of ${total}: ${item.name}, ${status.label}. Open in editor.`);
    refs.media.setAttribute('aria-current', active ? 'true' : 'false');
    if (refs.status.dataset.key !== status.key) {
      refs.status.dataset.key = status.key;
      refs.status.replaceChildren(statusChip(status));
    }
    refs.status.title = item.error || status.label;
    refs.overlay.hidden = !(item.busy === 'generating' || item.busy === 'scheduling');
    refs.left.disabled = index === 0;
    refs.right.disabled = index === total - 1;
    refs.remove.disabled = running;
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
    gallery.addEventListener('change', (event) => {
      if (!event.target.matches('[data-action="select"]')) return;
      const id = event.target.closest('.thumb-card')?.dataset.id;
      if (id) this.store.setSelected(id, event.target.checked);
    });
    gallery.addEventListener('keydown', (event) => {
      if (!event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
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
    this.store.remove(id);
    this.notifier.log('info', 'user', `Removed "${item.name}" from the gallery.`);
    showToast(`Removed "${truncate(item.name, 40)}".${hasContent(item) ? ' Its content is saved and returns if you upload it again.' : ''}`, 'info');
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
