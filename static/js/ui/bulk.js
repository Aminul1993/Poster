/** Edit selected: hashtags and category changed on every selected post at once, with Undo. */
import { showToast } from '../app.js';
import { LIMITS } from '../config.js';
import { countedResult, hasContent } from '../store.js';
import { applyScriptLang, charCount, el, pluralize, truncate } from '../util.js';
import { byId, setFieldError } from './dom.js';
import { View } from './view.js';

export class BulkEditView extends View {
  /**
   * The selected posts this can change (a caption, not in Buffer, not busy), and how many it leaves alone.
   * @returns {{editable: object[], scheduled: number, uncaptioned: number}}
   */
  targets() {
    const selected = this.store.selected();
    const mode = this.settings.mode;
    const scheduled = selected.filter((i) => countedResult(i, mode)).length;
    const uncaptioned = selected.filter((i) => !countedResult(i, mode) && !hasContent(i)).length;
    const editable = selected.filter((i) => !countedResult(i, mode) && hasContent(i) && !i.busy);
    return { editable, scheduled, uncaptioned };
  }

  /** @returns {string} the posts a change leaves alone, said once ("" when none) */
  leftAlone({ scheduled, uncaptioned }) {
    const parts = [
      scheduled ? `${pluralize(scheduled, 'scheduled post')} (edit ${scheduled === 1 ? 'it' : 'them'} in the editor)` : '',
      uncaptioned ? `${pluralize(uncaptioned, 'post')} without a caption yet` : ''
    ].filter(Boolean);
    return parts.length ? `${parts.join(' and ')} ${scheduled + uncaptioned === 1 ? 'is' : 'are'} left as ${scheduled + uncaptioned === 1 ? 'it is' : 'they are'}.` : '';
  }

  open() {
    if (this.scheduler.isRunning) {
      showToast('Wait for the current operation to finish, then edit the selected posts.', 'warning');
      return;
    }
    const targets = this.targets();
    if (!targets.editable.length) {
      showToast(`None of the selected posts can be edited here. ${this.leftAlone(targets)}`, 'info');
      return;
    }
    const count = targets.editable.length;
    byId('bulk-title').textContent = `Edit ${pluralize(count, 'selected post')}`;
    byId('bulk-sub').textContent = `Changes apply to ${count === 1 ? 'this post' : `all ${count}`}. ${this.leftAlone(targets)}`.trim();
    byId('bulk-apply-label').textContent = `Apply to ${pluralize(count, 'post')}`;
    byId('bulk-add').value = '';
    byId('bulk-category').value = '';
    for (const id of ['bulk-add', 'bulk-category']) setFieldError(id, null);
    byId('bulk-error').hidden = true;
    this.renderRemovable(targets.editable);
    byId('bulk-dialog').showModal();
    byId('bulk-add').focus();
  }

  /** The hashtags the selected posts use, most used first, each one a box to tick for removal. */
  renderRemovable(items) {
    const counts = new Map();
    for (const item of items) {
      for (const tag of item.content.hashtags) {
        const key = tag.toLowerCase();
        counts.set(key, { tag: counts.get(key)?.tag || tag, n: (counts.get(key)?.n || 0) + 1 });
      }
    }
    const tags = [...counts.values()].sort((a, b) => b.n - a.n || a.tag.localeCompare(b.tag));
    const box = byId('bulk-remove');
    if (!tags.length) {
      box.replaceChildren(el('p', { class: 'hint', text: 'None of these posts has a hashtag yet.' }));
      return;
    }
    box.replaceChildren(...tags.map(({ tag, n }) => {
      const text = el('span', { class: 'bulk-tag-text', text: tag });
      applyScriptLang(text, tag);
      return el('label', { class: 'bulk-tag' }, [
        el('input', { type: 'checkbox', value: tag, 'aria-label': `Remove ${tag} (on ${pluralize(n, 'post')})` }),
        text,
        el('span', { class: 'bulk-tag-count', 'aria-hidden': 'true', text: String(n) })
      ]);
    }));
  }

  bind() {
    const dialog = byId('bulk-dialog');
    byId('bulk-edit').addEventListener('click', () => this.open());
    byId('bulk-cancel').addEventListener('click', () => dialog.close());
    byId('bulk-form').addEventListener('submit', (event) => { event.preventDefault(); this.apply(); });
    byId('bulk-add').addEventListener('input', () => setFieldError('bulk-add', null));
    byId('bulk-category').addEventListener('input', () => setFieldError('bulk-category', null));
  }

  /** Validates the dialog, changes every editable selected post, and offers Undo. */
  apply() {
    const add = [];
    const invalid = [];
    for (const part of byId('bulk-add').value.split(/[\s,;]+/).filter(Boolean)) {
      const tag = this.validation.normalizeHashtag(part);
      if (!tag) invalid.push(`"${truncate(part, 30)}"`);
      else if (!add.some((t) => t.toLowerCase() === tag.toLowerCase())) add.push(tag);
    }
    const removed = [...byId('bulk-remove').querySelectorAll('input:checked')].map((input) => input.value);
    const remove = new Set(removed.map((tag) => tag.toLowerCase()));
    const category = byId('bulk-category').value.trim();
    setFieldError('bulk-add', invalid.length ? `${invalid.join(', ')} ${invalid.length === 1 ? 'is not a hashtag' : 'are not hashtags'}: use letters in any script, numbers or _.` : null);
    setFieldError('bulk-category', charCount(category) > LIMITS.maxCategoryChars ? `Keep the category under ${LIMITS.maxCategoryChars} characters.` : null);
    const nothing = !add.length && !remove.size && !category;
    byId('bulk-error').hidden = !nothing || invalid.length > 0;
    byId('bulk-error').textContent = 'Add a hashtag, tick one to remove, or enter a category.';
    if (invalid.length) { byId('bulk-add').focus(); return; }
    if (charCount(category) > LIMITS.maxCategoryChars) { byId('bulk-category').focus(); return; }
    if (nothing) { byId('bulk-add').focus(); return; }

    const before = new Map();
    let full = 0;
    for (const item of this.targets().editable) {
      const tags = item.content.hashtags.filter((t) => !remove.has(t.toLowerCase()));
      let missed = false;
      for (const tag of add) {
        if (tags.some((t) => t.toLowerCase() === tag.toLowerCase())) continue;
        if (tags.length >= LIMITS.maxHashtags) { missed = true; continue; }
        tags.push(tag);
      }
      if (missed) full += 1;
      const content = { ...item.content, hashtags: tags, category: category || item.content.category };
      if (JSON.stringify(content) === JSON.stringify(item.content)) continue;
      before.set(item.id, item.content);
      this.store.update(item.id, { content }, { reason: 'bulk-edit' });
    }
    byId('bulk-dialog').close();

    const what = [
      add.length ? `added ${add.join(' ')}` : '',
      removed.length ? `removed ${removed.join(' ')}` : '',
      category ? `category ${category}` : ''
    ].filter(Boolean).join('; ');
    if (!before.size) {
      showToast(`Nothing changed: the selected posts already had these hashtags and category${full ? `, or were full at ${LIMITS.maxHashtags} hashtags` : ''}.`, 'info');
      return;
    }
    this.notifier.log('info', 'user', `Edited ${pluralize(before.size, 'post')} at once: ${what}.`);
    const after = new Map([...before.keys()].map((id) => [id, this.store.get(id)?.content]));
    showToast(`Updated ${pluralize(before.size, 'post')}: ${what}.${full ? ` ${pluralize(full, 'post')} already had ${LIMITS.maxHashtags} hashtags, so not every new one fit.` : ''}`, 'success', {
      timeout: 8000,
      action: { label: 'Undo', onClick: () => this.undo(before, after) }
    });
  }

  /** Puts back what the posts had, unless one was changed again since (that edit is kept). */
  undo(before, after) {
    let restored = 0;
    for (const [id, content] of before) {
      const item = this.store.get(id);
      if (!item || item.content !== after.get(id) || countedResult(item, this.settings.mode)) continue;
      this.store.update(id, { content }, { reason: 'bulk-edit' });
      restored += 1;
    }
    if (restored) this.notifier.log('info', 'user', `Undid the edit to ${pluralize(restored, 'post')}.`);
    if (restored < before.size) showToast(`${pluralize(before.size - restored, 'post')} changed again since, so ${before.size - restored === 1 ? 'it keeps' : 'they keep'} the newer version.`, 'info');
  }
}
