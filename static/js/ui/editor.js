/** The post editor: captions, hashtags, category, score, publish time and media URL of the active image. */
import { generateContentForImage, scheduleSinglePost } from '../app.js';
import { LIMITS, SERVER } from '../config.js';
import { showThumbnail } from '../image.js';
import { serviceMeta } from '../services/buffer.js';
import { emptyContent, hasContent, itemStatus } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { charCount, clamp, el, formatBytes, icon, relativeTime, truncate } from '../util.js';
import { byId, colorDot, setFieldError, statusChip } from './dom.js';
import { View } from './view.js';

export class EditorView extends View {
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
    byId('ed-name').textContent = item.name;
    byId('ed-name').title = item.name;
    byId('ed-size').textContent = formatBytes(item.size);
    byId('ed-dims').textContent = `${item.width} × ${item.height}`;

    const busy = item.busy === 'generating' || item.busy === 'scheduling';
    const running = this.scheduler.isRunning;
    byId('ed-fieldset').disabled = busy;
    byId('editor-busy').hidden = !busy;
    byId('editor-busy-label').textContent = item.busy === 'scheduling' ? 'Scheduling post…' : 'Generating content…';
    byId('ed-generate').disabled = running;
    byId('ed-generate-label').textContent = hasContent(item) ? 'Regenerate content' : 'Generate content';
    byId('ed-schedule-post').disabled = running;

    const content = item.content || emptyContent();
    this.setValue('ed-description', content.description);
    this.setValue('ed-alt', content.alternative_description);
    document.querySelector(`input[name="ed-use"][value="${item.useAlternative ? 'alternative' : 'primary'}"]`).checked = true;
    this.renderTags(content.hashtags);
    this.setValue('ed-category', content.category);
    this.setValue('ed-score', String(content.engagement_score ?? 50));
    this.setValue('ed-schedule', item.scheduleLocal || '');
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
    resultBox.hidden = !item.result;
    if (item.result) {
      const r = item.result;
      const when = TimeZoneUtil.format(new Date(r.dueAt), r.timezone || s.timezone);
      byId('ed-result-title').textContent = `${r.mode === 'demo' ? 'Scheduled in Demo mode' : 'Scheduled in Buffer'} for ${when}`;
      const lines = [`Channels: ${(r.channels || []).join(', ') || '—'}`, `Post IDs: ${(r.postIds || []).join(', ') || '—'}`];
      if (r.failures?.length) lines.push(`Failed on: ${r.failures.map((f) => `${f.name} (${f.message})`).join('; ')}`);
      byId('ed-result-text').textContent = lines.join(' · ');
      resultBox.classList.toggle('is-success', !r.partial);
      resultBox.classList.toggle('is-info', Boolean(r.partial));
    }
    this.refreshDerived();
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
    byId('ed-status').replaceChildren(statusChip(itemStatus(item)));

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

    const score = Number(content.engagement_score) || 50;
    const scoreOut = byId('ed-score-value');
    scoreOut.textContent = String(score);
    scoreOut.className = `score-value ${score >= 70 ? 'is-high' : score >= 40 ? 'is-mid' : 'is-low'}`;

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

    const text = this.scheduler.composeText(item);
    const preview = byId('ed-preview');
    preview.textContent = text || 'Your post preview appears here once there is a caption.';
    preview.classList.toggle('is-empty', !text);
    const length = charCount(text);
    const previewCount = byId('ed-preview-count');
    previewCount.textContent = length ? `${length} characters${length > 280 ? ' · over X’s 280 limit' : ''}` : '';
    previewCount.classList.toggle('is-over', length > s.descMax + LIMITS.previewSlackChars);
  }

  /** @param {string[]} tags */
  renderTags(tags) {
    const box = byId('ed-tags');
    const input = byId('ed-tag-input');
    box.querySelectorAll('.tag').forEach((node) => node.remove());
    for (const tag of tags) {
      box.insertBefore(el('span', { class: 'tag' }, [
        el('span', { class: 'tag-text', text: tag }),
        el('button', { type: 'button', 'aria-label': `Remove ${tag}`, dataset: { tag } }, [icon('x')])
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
    byId('ed-score').addEventListener('input', (e) => this.updateContentField('engagement_score', clamp(Number(e.target.value) || 50, 1, 100)));
    document.querySelectorAll('input[name="ed-use"]').forEach((radio) => radio.addEventListener('change', (e) => {
      if (e.target.checked) this.updateItemField({ useAlternative: e.target.value === 'alternative' });
    }));
    byId('ed-schedule').addEventListener('input', (e) => this.updateItemField({ scheduleLocal: e.target.value }));
    byId('ed-media-url').addEventListener('input', (e) => this.updateItemField({ mediaUrl: e.target.value.trim() }));
    byId('ed-prev').addEventListener('click', () => this.stepActive(-1));
    byId('ed-next').addEventListener('click', () => this.stepActive(1));
    byId('ed-generate').addEventListener('click', () => generateContentForImage());
    byId('ed-schedule-post').addEventListener('click', () => scheduleSinglePost());
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
      ...patch, error: clears ? null : item.error, errorHint: clears ? '' : item.errorHint, errorPhase: clears ? null : item.errorPhase
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
      if (!tag) { problems.push(`"${truncate(part, 30)}" is not a valid hashtag (use letters, numbers or _).`); continue; }
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

