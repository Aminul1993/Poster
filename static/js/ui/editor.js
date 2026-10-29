/** The post editor: captions, hashtags, category, score, publish time and media URL of the active image. */
import { generateContentForImage, scheduleSinglePost, showToast } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { showThumbnail } from '../image.js';
import { serviceMeta } from '../services/buffer.js';
import { countedResult, emptyContent, hasContent, itemStatus, needsTime } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { applyScriptLang, charCount, el, formatBytes, icon, relativeTime, scriptRuns, truncate } from '../util.js';
import { byId, colorDot, setFieldError, statusChip } from './dom.js';
import { splitPostText } from './posts.js';
import { View } from './view.js';

/** @param {object} item @param {object} settings @returns {boolean} whether a post needs the owner before it can go out */
function needsAttention(item, settings) {
  return !item.busy && (Boolean(item.error) || !hasContent(item) || needsTime(item, settings));
}

export class EditorView extends View {
  constructor(ui) {
    super(ui);
    /** @type {Set<string>} posts the owner chose to write by hand before any caption existed */
    this.writing = new Set();
    this.announcedId = null;
  }

  /** Full editor render for the active image. */
  render() {
    const item = this.store.active;
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
    // A post already in Buffer is not edited here: Buffer would keep the old text while this showed the new.
    const locked = Boolean(countedResult(item, s.mode));
    byId('ed-fieldset').disabled = busy || locked;
    // Before there is a caption, the editor offers the two ways to get one instead of an empty form.
    editor.classList.toggle('is-blank', !hasContent(item) && !item.busy && !this.writing.has(item.id));
    byId('editor-busy').hidden = !busy;
    byId('editor-busy-label').textContent = item.busy === 'scheduling' ? 'Scheduling post…' : 'Generating content…';
    byId('ed-generate').disabled = running || locked;
    byId('ed-generate-label').textContent = hasContent(item) ? 'Regenerate content' : 'Generate content';
    byId('ed-restore').hidden = !item.previousContent;
    byId('ed-restore').disabled = running || busy || locked;
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
        lines = ['Its posts were deleted. Give it a new time to schedule it again.'];
      } else if (!counts) {
        title = 'Scheduled in Demo mode only';
        lines = ['This was a rehearsal: the post is not in Buffer. Schedule it to publish it for real.'];
      } else {
        title = `${r.mode === 'demo' ? 'In Demo mode (not in Buffer)' : 'In Buffer'} · ${TimeZoneUtil.format(new Date(r.dueAt), r.timezone || s.timezone)}`;
        lines = [`On ${(r.channels || []).join(', ') || 'no channel'}.`];
        if (r.failures?.length) lines.push(`Failed on: ${r.failures.map((f) => `${f.name} (${f.message})`).join('; ')}.`);
        lines.push(r.edited ? 'Edited in Scheduled posts; shown as it is now. Locked here.' : 'Locked here: edit it in Scheduled posts.');
      }
      byId('ed-result-title').textContent = title;
      byId('ed-result-text').textContent = lines.join(' ');
      byId('ed-result-edit').hidden = !counts || !(r.postIds || []).length;
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
    byId('ed-schedule-post').disabled = this.scheduler.isRunning;
    byId('ed-schedule-label').textContent = scheduled ? 'Schedule again' : 'Schedule post';
  }

  /** "Next to fix" with what is wrong across the other posts: missing captions, missing times, failures. */
  renderAttention(items, item) {
    const others = items.filter((i) => i.id !== item.id && needsAttention(i, this.settings));
    byId('ed-attention').hidden = !others.length;
    byId('ed-attention-count').textContent = String(others.length);
    const failed = others.filter((i) => i.error).length;
    const noCaption = others.filter((i) => !i.error && !hasContent(i)).length;
    const noTime = others.length - failed - noCaption;
    byId('ed-attention-detail').textContent = [
      noTime ? `${noTime} need${noTime === 1 ? 's' : ''} a time` : '',
      noCaption ? `${noCaption} without a caption` : '',
      failed ? `${failed} failed` : ''
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
    byId('ed-score-value').textContent = hasContent(item) && score >= 1 ? `${Math.round(score)} / 100` : '—';

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
    byId('ed-result-edit').addEventListener('click', () => this.editScheduled());
    byId('ed-write').addEventListener('click', () => {
      if (!this.store.active) return;
      this.writing.add(this.store.active.id);
      this.render();
      byId('ed-description').focus();
    });
    byId('ed-channels-edit').addEventListener('click', () => this.ui.openSettings('settings-buffer'));
    byId('ed-swap').addEventListener('click', () => {
      const item = this.store.active;
      if (!item || item.busy) return;
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
          event.preventDefault();
          this.addTags(tagInput.value);
          tagInput.value = '';
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
      this.addTags(text);
    });
    tagInput.addEventListener('blur', () => {
      if (tagInput.value.trim()) { this.addTags(tagInput.value); tagInput.value = ''; }
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

  /** Opens the scheduled post's own edit dialog, which changes it in Buffer. */
  editScheduled() {
    const ids = this.store.active?.result?.postIds || [];
    const copies = this.feed.posts.filter((p) => ids.includes(p.postId));
    if (copies.length) {
      this.ui.posts.openDialog(copies);
      return;
    }
    byId('schedules-section').scrollIntoView({ block: 'start' });
    showToast('This post is not in the list that is loaded. Refresh Scheduled posts, then edit it there.', 'info');
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
    if (!item || item.busy) return;
    const content = { ...(item.content || emptyContent()), [field]: value };
    this.store.update(item.id, { content, error: null, errorHint: '', errorPhase: null }, { reason: 'edit' });
    this.refreshDerived();
  }

  /** Updates non-content fields of the active item from the editor. */
  updateItemField(patch) {
    const item = this.store.active;
    if (!item || item.busy) return;
    const clears = item.errorPhase === 'schedule';
    this.store.update(item.id, {
      ...patch, error: clears ? null : item.error, errorHint: clears ? '' : item.errorHint, errorPhase: clears ? null : item.errorPhase,
      // A post deleted from Buffer comes back into the batch once it has a new time.
      ...(item.result?.removed && patch.scheduleLocal ? { result: null } : {})
    }, { reason: 'edit' });
    byId('ed-alert').hidden = !this.store.active.error;
    this.refreshDerived();
  }

  /** @param {string} text one or more hashtags separated by spaces or commas */
  addTags(text) {
    const item = this.store.active;
    if (!item || item.busy) return;
    const content = { ...(item.content || emptyContent()) };
    const tags = [...content.hashtags];
    const problems = [];
    for (const part of String(text).split(/[\s,;]+/).filter(Boolean)) {
      const tag = this.validation.normalizeHashtag(part);
      if (!tag) { problems.push(`"${truncate(part, 30)}" is not a valid hashtag (use letters in any script, numbers or _).`); continue; }
      if (tags.some((t) => t.toLowerCase() === tag.toLowerCase())) { problems.push(`${tag} is already added.`); continue; }
      if (tags.length >= LIMITS.maxHashtags) { problems.push(`The maximum is ${LIMITS.maxHashtags} hashtags.`); break; }
      tags.push(tag);
    }
    content.hashtags = tags;
    this.store.update(item.id, { content }, { reason: 'edit' });
    this.renderTags(tags);
    this.refreshDerived();
    if (problems.length) setFieldError('ed-tags', problems.join(' '), byId('ed-tags'));
  }

  /** @param {string} tag */
  removeTag(tag) {
    const item = this.store.active;
    if (!item || item.busy || !item.content) return;
    const content = { ...item.content, hashtags: item.content.hashtags.filter((t) => t !== tag) };
    this.store.update(item.id, { content }, { reason: 'edit' });
    this.renderTags(content.hashtags);
    this.refreshDerived();
  }
}

