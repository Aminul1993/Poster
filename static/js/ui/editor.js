/** The post editor: captions, hashtags, category, score, publish time and media URL of the active image. */
import { generateContentForImage, scheduleSinglePost, showToast } from '../app.js';
import { LANGUAGES, LIMITS, SERVER } from '../config.js';
import { showThumbnail } from '../image.js';
import { serviceMeta } from '../services/buffer.js';
import { contentFromText, countedResult, emptyContent, hasContent, itemStatus, needsTime } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { applyScriptLang, charCount, el, formatBytes, icon, pluralize, relativeTime, scriptLang, scriptRuns, splitPostText, truncate } from '../util.js';
import { byId, colorDot, setFieldError, statusChip } from './dom.js';
import { View } from './view.js';

const TAGS_HINT = 'Enter, comma or space adds a tag · ← from the empty field moves to the tags, Delete removes one';

/** @param {object} item @param {object} settings @returns {boolean} whether a post needs the owner before it can go out */
function needsAttention(item, settings) {
  return !item.busy && (Boolean(item.error) || !hasContent(item) || needsTime(item, settings) || Boolean(item.result?.removed));
}

export class EditorView extends View {
  constructor(ui) {
    super(ui);
    /** @type {Set<string>} posts the owner chose to write by hand before any caption existed */
    this.writing = new Set();
    this.announcedId = null;
    /** @type {{id: string, snapshot: object}|null} the scheduled post being edited in place, and how it was before */
    this.bufferEdit = null;
    this.savingBuffer = false;
  }

  /** Full editor render for the active image. */
  render() {
    const item = this.store.active;
    if (this.bufferEdit && this.bufferEdit.id !== item?.id && !this.savingBuffer) {
      // Leaving a post mid-edit puts it back as Buffer has it, so the draft never drifts from Buffer.
      const edit = this.bufferEdit;
      this.bufferEdit = null;
      const other = this.store.get(edit.id);
      if (other) {
        this.store.update(edit.id, { ...edit.snapshot, showErrors: false }, { reason: 'status' });
        showToast(`Your unsaved edit to "${truncate(other.name, 40)}" was discarded; Buffer still has the earlier version.`, 'info');
      }
    }
    const items = this.store.all();
    const editor = byId('editor');
    const empty = byId('editor-empty');
    if (!item) {
      editor.hidden = true;
      empty.hidden = false;
      byId('editor-empty-title').textContent = items.length ? 'Select an image' : 'Nothing to edit yet';
      byId('editor-empty-text').textContent = items.length
        ? 'Choose an image in the gallery to generate and edit its post.'
        : 'Upload images, then pick one in the gallery to generate and edit its post.';
      return;
    }
    editor.hidden = false;
    empty.hidden = true;
    const s = this.settings;
    const index = this.store.indexOf(item.id);
    const image = byId('ed-image');
    showThumbnail(image, item);
    image.alt = `Preview of ${item.name}`;
    byId('ed-position').textContent = `${index + 1} of ${items.length}`;
    byId('ed-prev').disabled = index <= 0;
    byId('ed-next').disabled = index >= items.length - 1;
    this.renderAttention(items, item);
    if (this.announcedId !== item.id) {
      // One announcement per post opened (Alt+↑/↓, Next to fix, the gallery), not on every re-render.
      this.announcedId = item.id;
      byId('ed-announce').textContent = `${item.name}, ${itemStatus(item, this.settings).label}, post ${index + 1} of ${items.length}`;
    }
    byId('ed-name').textContent = item.name;
    byId('ed-name').title = item.name;
    byId('ed-size').textContent = formatBytes(item.size);
    byId('ed-dims').textContent = `${item.width} × ${item.height}`;

    const busy = item.busy === 'generating' || item.busy === 'scheduling';
    const running = this.scheduler.isRunning;
    // A post already in Buffer is read-only until "Edit in Buffer", which saves straight to every channel copy.
    const scheduled = Boolean(countedResult(item, s.mode));
    // Deleted in Scheduled posts while being edited here: it is an ordinary draft again.
    if (!scheduled && this.bufferEdit?.id === item.id && !this.savingBuffer) this.bufferEdit = null;
    const editingBuffer = scheduled && this.bufferEdit?.id === item.id;
    const locked = scheduled && !editingBuffer;
    byId('ed-fieldset').disabled = busy || this.savingBuffer;
    // A post in Buffer is read-only rather than disabled: its words stay readable, and reachable with Tab.
    for (const id of ['ed-description', 'ed-alt', 'ed-tag-input', 'ed-schedule']) byId(id).readOnly = locked;
    // Only the words and the time live in Buffer; category, image URL and channels stay as scheduled.
    for (const id of ['ed-category', 'ed-media-url']) byId(id).readOnly = scheduled;
    byId('ed-channels-edit').disabled = scheduled;
    byId('ed-swap').disabled = locked;
    for (const radio of document.querySelectorAll('input[name="ed-use"]')) radio.disabled = locked;
    editor.classList.toggle('is-locked', locked);
    editor.classList.toggle('is-buffer-edit', editingBuffer);
    for (const note of editor.querySelectorAll('.lock-text')) note.textContent = item.result?.mode === 'demo' ? 'Scheduled' : 'In Buffer';
    // Before there is a caption, the editor offers the two ways to get one instead of an empty form.
    editor.classList.toggle('is-blank', !hasContent(item) && !item.busy && !this.writing.has(item.id));
    byId('editor-busy').hidden = !busy;
    byId('editor-busy-label').textContent = item.busy === 'scheduling' ? 'Scheduling post…' : 'Generating content…';
    byId('ed-generate').disabled = running || scheduled;
    // This post's caption language: empty follows the batch's, which the first option names.
    const language = byId('ed-language');
    language.options[0].textContent = `Batch setting (${LANGUAGES[s.language] || 'English'})`;
    language.value = item.language || '';
    language.disabled = busy || scheduled;
    byId('ed-generate-label').textContent = hasContent(item) ? 'Regenerate content' : 'Generate content';
    byId('ed-restore').hidden = !item.previousContent;
    byId('ed-restore').disabled = running || busy || scheduled;
    this.renderScheduleButton(item);

    const content = item.content || emptyContent();
    this.setValue('ed-description', content.description);
    this.setValue('ed-alt', content.alternative_description);
    document.querySelector(`input[name="ed-use"][value="${item.useAlternative ? 'alternative' : 'primary'}"]`).checked = true;
    this.renderTags(content.hashtags);
    this.setValue('ed-category', content.category);
    this.setValue('ed-schedule', item.scheduleLocal || '');
    // Once in Buffer the editor mirrors Buffer: an edit made in Scheduled posts shows here, read-only.
    if (locked && item.result.text) {
      const { caption, tags } = splitPostText(item.result.text);
      this.setValue(item.useAlternative ? 'ed-alt' : 'ed-description', caption);
      this.renderTags(tags ? tags.split(' ') : []);
    }
    if (locked && item.result.dueAt) this.setValue('ed-schedule', TimeZoneUtil.toLocalInput(new Date(item.result.dueAt), s.timezone));
    byId('ed-schedule').min = TimeZoneUtil.toLocalInput(new Date(), s.timezone);
    this.setValue('ed-media-url', item.mediaUrl || '');
    byId('ed-tz-label').textContent = s.timezone;
    byId('ed-description').maxLength = s.descMax;
    byId('ed-alt').maxLength = s.descMax;
    this.renderChannels();

    const alert = byId('ed-alert');
    alert.hidden = !item.error;
    if (item.error) {
      byId('ed-alert-title').textContent = item.errorPhase === 'generate' ? 'Content generation failed' : 'Not scheduled';
      byId('ed-alert-text').textContent = [item.error, item.errorHint].filter(Boolean).join(' ');
    }

    const resultBox = byId('ed-result');
    const r = item.result;
    resultBox.hidden = !r;
    if (r) {
      const counts = Boolean(countedResult(item, s.mode));
      let title;
      let lines;
      if (r.removed) {
        title = 'Removed from Buffer';
        const due = item.scheduleLocal ? TimeZoneUtil.zonedToUtc(item.scheduleLocal, s.timezone) : null;
        lines = [due && due.date.getTime() > Date.now()
          ? `Its posts were deleted. Schedule post puts it back at ${TimeZoneUtil.format(due.date, s.timezone)}, or pick a new time.`
          : 'Its posts were deleted. Pick a new time, then Schedule post.'];
      } else if (!counts) {
        title = 'Scheduled in Demo mode only';
        lines = ['This was a rehearsal: the post is not in Buffer. Schedule it to publish it for real.'];
      } else {
        title = `${r.mode === 'demo' ? 'In Demo mode (not in Buffer)' : 'In Buffer'} · ${TimeZoneUtil.format(new Date(r.dueAt), r.timezone || s.timezone)}`;
        lines = [`On ${(r.channels || []).join(', ') || 'no channel'}.`];
        if (r.failures?.length) lines.push(`Failed on: ${r.failures.map((f) => `${f.name} (${f.message})`).join('; ')}.`);
        const place = r.mode === 'demo' ? 'in Demo mode' : 'in Buffer';
        lines.push(editingBuffer
          ? `Editing: change the caption, hashtags or time, then save ${r.mode === 'demo' ? 'in Demo mode' : 'to Buffer'}.`
          : r.edited ? `Edited ${place}; shown as it is now.` : `Shown as it is ${place}.`);
      }
      byId('ed-result-title').textContent = title;
      byId('ed-result-text').textContent = lines.join(' ');
      byId('ed-result-edit').hidden = !counts || editingBuffer || !(r.postIds || []).length;
      byId('ed-result-edit-label').textContent = r.mode === 'demo' ? 'Edit post' : 'Edit in Buffer';
      byId('ed-buffer-save').hidden = !editingBuffer;
      byId('ed-buffer-cancel').hidden = !editingBuffer;
      byId('ed-buffer-save-label').textContent = r.mode === 'demo' ? 'Save in Demo mode' : 'Save to Buffer';
      resultBox.classList.toggle('is-success', counts && !r.partial);
      resultBox.classList.toggle('is-info', !counts || Boolean(r.partial));
    }
    this.refreshDerived();
  }

  /**
   * The single-post Schedule button. It is never the indigo one: the panel's stage button (All images, Apply
   * to all, All ready) is the next action for the batch.
   * @param {object} item
   */
  renderScheduleButton(item) {
    const scheduled = Boolean(countedResult(item, this.settings.mode));
    byId('ed-schedule-post').disabled = this.scheduler.isRunning || this.bufferEdit?.id === item.id;
    byId('ed-schedule-label').textContent = scheduled ? 'Schedule again' : 'Schedule post';
  }

  /** "Next to fix" with what is wrong across the other posts: missing captions, missing times, failures. */
  renderAttention(items, item) {
    const others = items.filter((i) => i.id !== item.id && needsAttention(i, this.settings));
    byId('ed-attention').hidden = !others.length;
    byId('ed-attention-count').textContent = String(others.length);
    const failed = others.filter((i) => i.error).length;
    const noCaption = others.filter((i) => !i.error && !hasContent(i)).length;
    const removed = others.filter((i) => !i.error && hasContent(i) && i.result?.removed).length;
    const noTime = others.length - failed - noCaption - removed;
    byId('ed-attention-detail').textContent = [
      noTime ? `${noTime} need${noTime === 1 ? 's' : ''} a time` : '',
      noCaption ? `${noCaption} without a caption` : '',
      failed ? `${failed} failed` : '',
      removed ? `${removed} removed from Buffer` : ''
    ].filter(Boolean).join(' · ');
  }

  /** Sets an input value only when it changed (keeps the caret in place). */
  setValue(id, value) {
    const input = byId(id);
    if (input.value !== value) input.value = value;
  }

  /** Counters, previews, inline validation and status for the active item. */
  refreshDerived() {
    const item = this.store.active;
    if (!item) return;
    const s = this.settings;
    const content = item.content || emptyContent();
    const show = item.showErrors;
    byId('ed-status').replaceChildren(statusChip(itemStatus(item, s)));
    this.renderScheduleButton(item);
    applyScriptLang(byId('ed-description'), content.description);
    byId('ed-count-hint').hidden = !scriptLang(content.description);
    applyScriptLang(byId('ed-alt'), content.alternative_description);

    const descLength = charCount(content.description);
    const altLength = charCount(content.alternative_description);
    const setCounter = (id, length) => {
      const counter = byId(id);
      counter.textContent = `${length} / ${s.descMax}`;
      counter.classList.toggle('is-over', length > s.descMax || (length > 0 && length < s.descMin));
    };
    setCounter('ed-description-count', descLength);
    setCounter('ed-alt-count', altLength);

    setFieldError('ed-description', (show || descLength) ? this.validation.validateCaption(content.description, 'Caption', s) : null);
    const altNeeded = item.useAlternative && (show || altLength);
    setFieldError('ed-alt', altNeeded ? this.validation.validateCaption(content.alternative_description, 'Alternative caption', s) : null);
    setFieldError('ed-tags', this.validation.validateHashtags(content.hashtags), byId('ed-tags'));
    setFieldError('ed-category', charCount(content.category) > LIMITS.maxCategoryChars ? `Keep the category under ${LIMITS.maxCategoryChars} characters.` : null);

    const score = Number(content.engagement_score);
    const estimate = hasContent(item) && score >= 1 ? `${Math.round(score)} / 100` : '';
    byId('ed-score-value').textContent = estimate || '—';
    // The folded group still says what it holds, so the category is never out of sight.
    const category = String(content.category || '').trim();
    byId('ed-notes-summary').textContent = hasContent(item) ? `${category || 'No category'} · ${estimate ? `${estimate} estimate` : 'no estimate'}` : '';
    if (charCount(content.category) > LIMITS.maxCategoryChars) byId('ed-notes').open = true;

    const hint = byId('ed-schedule-hint');
    if (item.scheduleLocal) {
      const check = this.validation.validateSchedule(item.scheduleLocal, s);
      if (check.valid) {
        hint.textContent = `${TimeZoneUtil.format(check.date, s.timezone)} · ${relativeTime(check.date)}`;
        hint.hidden = false;
        setFieldError('ed-schedule', null);
      } else {
        hint.hidden = true;
        setFieldError('ed-schedule', item.result ? null : check.error);
      }
    } else {
      hint.textContent = 'Pick a date and time, or use the schedule planner above.';
      hint.hidden = false;
      setFieldError('ed-schedule', show ? 'Choose a date and time to publish.' : null);
    }
    setFieldError('ed-media-url', this.validation.validateMediaUrl(item.mediaUrl));
    const mediaHint = byId('ed-media-hint');
    if (item.mediaUrl) mediaHint.textContent = 'This URL is attached to the post in Buffer.';
    else if (item.uploadedUrl) mediaHint.textContent = `Uploaded earlier: ${truncate(item.uploadedUrl, 70)}`;
    else if (s.mode === 'demo') mediaHint.textContent = 'Demo mode simulates hosting. In Live mode, Buffer needs a public URL or a media host.';
    else if (SERVER.media.configured) mediaHint.textContent = `Leave empty to upload this image to ${SERVER.media.host || 'the media host'} when scheduling.`;
    else mediaHint.textContent = 'Buffer attaches images from public URLs. Paste one here; no media host is configured on the server.';

    const text = countedResult(item, s.mode)?.text || this.scheduler.composeText(item);
    const preview = byId('ed-preview');
    if (text) scriptRuns(preview, text);
    else applyScriptLang(preview, preview.textContent = 'Your post preview appears here once there is a caption.');
    preview.classList.toggle('is-empty', !text);
    const length = charCount(text);
    const previewCount = byId('ed-preview-count');
    const overX = length > 280 && this.postsToX();
    previewCount.textContent = length ? `${length} characters${overX ? ' · over X’s 280 limit' : ''}` : '';
    previewCount.classList.toggle('is-over', overX || length > s.descMax + LIMITS.previewSlackChars);
  }

  /** @returns {boolean} whether one of the selected channels is X, the only one with a 280-character limit */
  postsToX() {
    const buffer = this.app.getBuffer();
    const channels = buffer.channels(this.settings);
    return buffer.selectedChannelIds(this.settings).some((id) => serviceMeta(channels.find((c) => c.id === id)?.service).label === 'X');
  }

  /** @param {string[]} tags */
  renderTags(tags) {
    const box = byId('ed-tags');
    const input = byId('ed-tag-input');
    box.querySelectorAll('.tag').forEach((node) => node.remove());
    for (const tag of tags) {
      const text = el('span', { class: 'tag-text', text: tag, title: tag });
      applyScriptLang(text, tag);
      box.insertBefore(el('span', { class: 'tag' }, [
        text,
        el('button', { type: 'button', tabindex: '-1', 'aria-label': `Remove ${tag}`, dataset: { tag } }, [icon('x')])
      ]), input);
    }
    const counter = byId('ed-tags-count');
    counter.textContent = `${tags.length} / ${LIMITS.maxHashtags}`;
    counter.classList.toggle('is-over', tags.length > LIMITS.maxHashtags);
  }

  renderChannels() {
    const s = this.settings;
    const buffer = this.app.getBuffer();
    const ids = buffer.selectedChannelIds(s);
    const box = byId('ed-channels');
    if (!ids.length) {
      box.replaceChildren(el('span', { class: 'chip chip-warning', text: 'No channels selected' }));
      return;
    }
    box.replaceChildren(...ids.map((id) => {
      const channel = buffer.channels(s).find((c) => c.id === id);
      const meta = serviceMeta(channel?.service);
      return el('span', { class: 'chip chip-neutral no-dot', title: channel ? `${meta.label} · ${channel.organizationName || ''}` : id }, [
        colorDot(meta.color),
        channel ? `${channel.name} · ${meta.label}` : `Channel ${truncate(id, 10)}`
      ]);
    }));
  }

  bind() {
    byId('ed-language').replaceChildren(el('option', { value: '', text: 'Batch setting' }),
      ...Object.entries(LANGUAGES).map(([value, label]) => el('option', { value, text: label })));
    byId('ed-language').addEventListener('change', (e) => {
      const item = this.store.active;
      if (item && !item.busy) this.store.update(item.id, { language: e.target.value }, { reason: 'edit' });
    });
    byId('ed-description').addEventListener('input', (e) => this.updateContentField('description', e.target.value));
    byId('ed-alt').addEventListener('input', (e) => this.updateContentField('alternative_description', e.target.value));
    byId('ed-category').addEventListener('input', (e) => this.updateContentField('category', e.target.value));
    document.querySelectorAll('input[name="ed-use"]').forEach((radio) => radio.addEventListener('change', (e) => {
      if (e.target.checked) this.updateItemField({ useAlternative: e.target.value === 'alternative' });
    }));
    byId('ed-schedule').addEventListener('input', (e) => this.updateItemField({ scheduleLocal: e.target.value }));
    byId('ed-media-url').addEventListener('input', (e) => this.updateItemField({ mediaUrl: e.target.value.trim() }));
    byId('ed-prev').addEventListener('click', () => this.stepActive(-1));
    byId('ed-next').addEventListener('click', () => this.stepActive(1));
    byId('ed-next-attention').addEventListener('click', () => this.nextNeedingAttention());
    document.addEventListener('keydown', (event) => {
      // Alt+↑ / Alt+↓ move between posts from anywhere, except where Alt+↓ opens a list (selects) or in dialogs.
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      if (event.target.closest?.('select, dialog') || !this.store.active) return;
      if (event.target.dataset?.field) {
        // In the List view the caret follows: the next row's caption or time, not just the editor.
        event.preventDefault();
        this.ui.gallery.focusAdjacent(event.target, event.key === 'ArrowUp' ? -1 : 1);
        return;
      }
      event.preventDefault();
      this.stepActive(event.key === 'ArrowUp' ? -1 : 1);
    });
    byId('ed-generate').addEventListener('click', () => generateContentForImage());
    byId('ed-schedule-post').addEventListener('click', () => scheduleSinglePost());
    byId('ed-restore').addEventListener('click', () => this.restorePrevious());
    byId('ed-result-edit').addEventListener('click', () => this.startBufferEdit());
    byId('ed-buffer-save').addEventListener('click', () => this.saveBufferEdit());
    byId('ed-buffer-cancel').addEventListener('click', () => this.cancelBufferEdit());
    byId('ed-blank-generate').addEventListener('click', () => generateContentForImage());
    byId('ed-write').addEventListener('click', () => {
      if (!this.store.active) return;
      this.writing.add(this.store.active.id);
      this.render();
      byId('ed-description').focus();
    });
    byId('ed-channels-edit').addEventListener('click', () => this.ui.openSettings('settings-buffer'));
    byId('ed-swap').addEventListener('click', () => {
      const item = this.store.active;
      if (!item || item.busy || this.isLocked(item)) return;
      const c = item.content || emptyContent();
      this.store.update(item.id, { content: { ...c, description: c.alternative_description, alternative_description: c.description } }, { reason: 'edit' });
      this.render();
    });
    byId('editor').addEventListener('submit', (event) => event.preventDefault());

    const tagInput = byId('ed-tag-input');
    const tagBox = byId('ed-tags');
    tagInput.addEventListener('keydown', (event) => {
      if (event.isComposing) return;
      if (['Enter', ',', ' ', ';'].includes(event.key) || (event.key === 'Tab' && tagInput.value.trim())) {
        if (tagInput.value.trim()) {
          const rest = this.addTags(tagInput.value);
          // Tab moves on when tags were left in the field; otherwise the cursor stays to add more.
          if (event.key !== 'Tab' || !rest) event.preventDefault();
          tagInput.value = rest;
        } else if (event.key !== 'Tab') {
          event.preventDefault();
        }
      } else if (event.key === 'Backspace' && !tagInput.value) {
        const tags = this.store.active?.content?.hashtags || [];
        if (tags.length) this.removeTag(tags[tags.length - 1]);
      }
    });
    tagInput.addEventListener('paste', (event) => {
      const text = event.clipboardData?.getData('text') || '';
      if (!/[\s,;]/.test(text.trim())) return;
      event.preventDefault();
      tagInput.value = this.addTags(text);
    });
    tagInput.addEventListener('blur', () => {
      if (tagInput.value.trim()) tagInput.value = this.addTags(tagInput.value);
    });
    // The hashtags are one tab stop: ← from the empty field reaches them, arrows move, Delete removes, → or Esc returns.
    tagBox.addEventListener('keydown', (event) => {
      const buttons = [...tagBox.querySelectorAll('button[data-tag]')];
      if (event.target === tagInput) {
        if (event.key === 'ArrowLeft' && !tagInput.value && buttons.length) {
          event.preventDefault();
          buttons[buttons.length - 1].focus();
        }
        return;
      }
      const index = buttons.indexOf(event.target.closest('button[data-tag]'));
      if (index === -1) return;
      if (event.key === 'ArrowLeft' && index > 0) {
        event.preventDefault();
        buttons[index - 1].focus();
      } else if (event.key === 'ArrowRight' || event.key === 'Escape') {
        event.preventDefault();
        (event.key === 'ArrowRight' && buttons[index + 1] ? buttons[index + 1] : tagInput).focus();
      } else if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        this.removeTag(buttons[index].dataset.tag);
        const rest = [...tagBox.querySelectorAll('button[data-tag]')];
        (rest[Math.min(index, rest.length - 1)] || tagInput).focus();
      }
    });
    tagBox.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tag]');
      if (button) { this.removeTag(button.dataset.tag); tagInput.focus(); return; }
      if (event.target === tagBox) tagInput.focus();
    });
  }

  /** @param {number} delta */
  stepActive(delta) {
    const index = this.store.indexOf(this.store.activeId);
    const next = this.store.all()[index + delta];
    if (next) this.store.setActive(next.id);
  }

  /** Opens the next post, after the open one and wrapping round, that needs a caption, a time or a fix. */
  nextNeedingAttention() {
    const items = this.store.all();
    const index = this.store.indexOf(this.store.activeId);
    const ordered = [...items.slice(index + 1), ...items.slice(0, Math.max(0, index))];
    const next = ordered.find((item) => needsAttention(item, this.settings));
    if (!next) return;
    this.store.setActive(next.id);
    this.ui.gallery.revealEditor({ focus: true });
  }

  /** @param {object} item @returns {boolean} whether the post is in Buffer and shown read-only (not being edited) */
  isLocked(item) {
    return Boolean(countedResult(item, this.settings.mode)) && this.bufferEdit?.id !== item.id;
  }

  /**
   * Edit in Scheduled posts, for a post from this batch: it opens here, where its caption, hashtags and time
   * are edited with the same fields as before it was scheduled. @param {string} id
   */
  editScheduled(id) {
    if (this.scheduler.isRunning) {
      showToast('Wait for the current operation to finish, then edit the post.', 'warning');
      return;
    }
    this.store.setActive(id);
    this.ui.gallery.revealEditor();
    this.startBufferEdit();
  }

  /** Unlocks a scheduled post's caption, hashtags and time; Save sends them to every channel copy. */
  startBufferEdit() {
    const item = this.store.active;
    if (!item || !countedResult(item, this.settings.mode) || this.scheduler.isRunning) return;
    const r = countedResult(item, this.settings.mode);
    this.bufferEdit = { id: item.id, snapshot: { content: item.content, useAlternative: item.useAlternative, scheduleLocal: item.scheduleLocal } };
    // The fields start from the post as Buffer has it now, which is what the locked editor showed.
    const fields = {};
    if (r.text) fields.content = contentFromText(item.content, r.text, item.useAlternative);
    if (r.dueAt) fields.scheduleLocal = TimeZoneUtil.toLocalInput(new Date(r.dueAt), this.settings.timezone);
    if (Object.keys(fields).length) this.store.update(item.id, fields, { reason: 'status' });
    this.render();
    byId('ed-description').focus();
  }

  /** Puts the post back as Buffer has it. */
  cancelBufferEdit() {
    const edit = this.bufferEdit;
    if (!edit || this.savingBuffer) return;
    this.bufferEdit = null;
    if (this.store.get(edit.id)) this.store.update(edit.id, { ...edit.snapshot, showErrors: false }, { reason: 'status' });
    this.render();
    byId('ed-result-edit').focus();
  }

  /** Sends the edited caption, hashtags and time to every channel copy of the post, then locks it again. */
  async saveBufferEdit() {
    const item = this.store.get(this.bufferEdit?.id);
    if (!item || this.savingBuffer) return;
    const check = this.validation.validatePost(item, this.settings);
    if (!check.valid) {
      this.store.update(item.id, { showErrors: true }, { reason: 'validation', persist: false });
      showToast(check.message, 'error', { title: 'Fix the post before saving' });
      return;
    }
    const r = item.result;
    const ids = r?.postIds || [];
    const where = this.app.getBuffer().isDemo ? 'in Demo mode (not in Buffer)' : 'to Buffer';
    // Only what changed is sent, as in the Scheduled-post dialog.
    const changes = {};
    const text = this.scheduler.composeText(item);
    if (text !== r.text) changes.text = text;
    if (!r.dueAt || Math.floor(check.date.getTime() / 60000) !== Math.floor(Date.parse(r.dueAt) / 60000)) changes.dueAt = check.date;
    if (!Object.keys(changes).length) {
      this.bufferEdit = null;
      showToast(where === 'to Buffer' ? 'Nothing changed, so Buffer was not updated.' : 'Nothing changed.', 'info');
      this.render();
      byId('ed-result-edit').focus();
      return;
    }
    this.savingBuffer = true;
    this.render();
    byId('ed-buffer-save').classList.add('is-loading');
    byId('ed-buffer-cancel').disabled = true;
    const failed = [];
    for (const id of ids) {
      try {
        await this.feed.edit(id, changes);
      } catch (error) {
        failed.push([error.message, error.hint].filter(Boolean).join(' '));
      }
    }
    this.savingBuffer = false;
    byId('ed-buffer-save').classList.remove('is-loading');
    byId('ed-buffer-cancel').disabled = false;
    if (failed.length) {
      // Copies that saved keep the new version; the edit stays open so the owner can try again.
      const saved = ids.length - failed.length;
      // Cancel now goes back to what the saved copies have, not to the text from before.
      const now = this.store.get(item.id);
      if (saved && now) this.bufferEdit.snapshot = { content: now.content, useAlternative: now.useAlternative, scheduleLocal: now.scheduleLocal };
      this.render();
      showToast(`${failed[0]}${saved ? ` Saved on ${saved} of ${ids.length} channels.` : ''}`, 'error', { title: 'Not every copy was saved' });
      return;
    }
    this.bufferEdit = null;
    showToast(`Saved ${where} on ${pluralize(ids.length, 'channel')}.`, 'success');
    this.render();
    byId('ed-result-edit').focus();
  }

  /** Puts back the content the last regeneration replaced (once). */
  restorePrevious() {
    const item = this.store.active;
    if (!item?.previousContent || item.busy || this.scheduler.isRunning) return;
    this.store.update(item.id, { content: item.previousContent, previousContent: null }, { reason: 'edit' });
    this.notifier.log('info', 'user', `Restored the previous content of "${item.name}".`);
    this.render();
  }

  /** Updates one content field of the active item from the editor. */
  updateContentField(field, value) {
    const item = this.store.active;
    if (!item || item.busy || this.isLocked(item)) return;
    const content = { ...(item.content || emptyContent()), [field]: value };
    this.store.update(item.id, { content, error: null, errorHint: '', errorPhase: null }, { reason: 'edit' });
    this.refreshDerived();
  }

  /** Updates non-content fields of the active item from the editor. */
  updateItemField(patch) {
    const item = this.store.active;
    if (!item || item.busy || this.isLocked(item)) return;
    const clears = item.errorPhase === 'schedule';
    this.store.update(item.id, {
      ...patch, error: clears ? null : item.error, errorHint: clears ? '' : item.errorHint, errorPhase: clears ? null : item.errorPhase,
      // A post deleted from Buffer comes back into the batch once it has a new time.
      ...(item.result?.removed && patch.scheduleLocal ? { result: null } : {})
    }, { reason: 'edit' });
    byId('ed-alert').hidden = !this.store.active.error;
    this.refreshDerived();
  }

  /**
   * @param {string} text one or more hashtags separated by spaces or commas
   * @returns {string} what could not be added, left in the field for the owner to fix
   */
  addTags(text) {
    const item = this.store.active;
    if (!item || item.busy || this.isLocked(item)) return '';
    const content = { ...(item.content || emptyContent()) };
    const tags = [...content.hashtags];
    const problems = [];
    const kept = [];
    let full = false;
    for (const part of String(text).split(/[\s,;]+/).filter(Boolean)) {
      const tag = this.validation.normalizeHashtag(part);
      if (!tag) { problems.push(`"${truncate(part, 30)}" is not a valid hashtag (use letters in any script, numbers or _).`); kept.push(part); continue; }
      if (tags.some((t) => t.toLowerCase() === tag.toLowerCase())) { problems.push(`${tag} is already added.`); continue; }
      if (tags.length >= LIMITS.maxHashtags) { full = true; kept.push(part); continue; }
      tags.push(tag);
    }
    content.hashtags = tags;
    this.store.update(item.id, { content }, { reason: 'edit' });
    this.renderTags(tags);
    this.refreshDerived();
    if (problems.length) setFieldError('ed-tags', problems.join(' '), byId('ed-tags'));
    // At the cap the set is still valid: a hint, not an error, and the extra tags wait in the field.
    const hint = byId('ed-tags-hint');
    hint.classList.toggle('is-warning', full);
    hint.textContent = full ? `The maximum is ${LIMITS.maxHashtags} hashtags. Remove one to add what is still in the field.` : TAGS_HINT;
    return kept.join(' ');
  }

  /** @param {string} tag */
  removeTag(tag) {
    const item = this.store.active;
    if (!item || item.busy || !item.content || this.isLocked(item)) return;
    const content = { ...item.content, hashtags: item.content.hashtags.filter((t) => t !== tag) };
    byId('ed-tags-hint').classList.remove('is-warning');
    byId('ed-tags-hint').textContent = TAGS_HINT;
    this.store.update(item.id, { content }, { reason: 'edit' });
    this.renderTags(content.hashtags);
    this.refreshDerived();
  }
}

