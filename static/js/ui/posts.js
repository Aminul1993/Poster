/** The Scheduled posts card and the Edit scheduled post dialog. */
import { showToast } from '../app.js';
import { LIMITS } from '../config.js';
import { BufferPostsFeed } from '../feed.js';
import { serviceMeta } from '../services/buffer.js';
import { countedResult } from '../store.js';
import { TimeZoneUtil } from '../time.js';
import { applyScriptLang, charCount, el, icon, pluralize, relativeTime, scriptRuns, splitPostText, truncate } from '../util.js';
import { byId, colorDot, setFieldError } from './dom.js';
import { View } from './view.js';

const STATUS_CHIPS = Object.freeze({
  scheduled: ['Scheduled', 'success'], needs_approval: ['Needs approval', 'warning'], sending: ['Publishing', 'info'],
  sent: ['Published', 'neutral'], error: ['Failed', 'danger'], draft: ['Draft', 'neutral']
});

/**
 * Buffer keeps one post per channel; the owner thinks in images. Copies that share their text and publish
 * time (to the minute) were scheduled together, so they are one post here. Order follows the feed.
 * @param {import('../services/buffer.js').BufferPost[]} posts
 * @returns {Array<{key: string, posts: import('../services/buffer.js').BufferPost[]}>}
 */
export function groupPosts(posts) {
  const groups = new Map();
  for (const post of posts) {
    const when = post.dueAt || post.sentAt;
    const key = when ? `${post.text.trim()}|${Math.floor(Date.parse(when) / 60000)}` : post.postId;
    if (!groups.has(key)) groups.set(key, { key, posts: [] });
    groups.get(key).posts.push(post);
  }
  return [...groups.values()];
}

export class PostsView extends View {
  constructor(ui) {
    super(ui);
    /** @type {{postIds: string[], text: string, dueLocal: string, zone: string, saving: boolean, toX: boolean}|null} the post being edited */
    this.postEdit = null;
    /** @type {Map<string, {key: string, node: HTMLElement}>} list rows by post, rebuilt only when they change */
    this.rows = new Map();
    /** @type {Set<string>} Buffer post IDs the last scheduling run created, highlighted after "View in Scheduled posts" */
    this.highlight = new Set();
  }

  /** The Scheduled posts card: posts as last read from Buffer, one row per post with its channel copies inside. */
  render() {
    const feed = this.feed;
    const groups = groupPosts(feed.posts);
    const list = byId('sched-list');
    byId('sched-count').textContent = `${groups.length}${feed.next ? '+' : ''}`;
    for (const input of document.querySelectorAll('input[name="sched-filter"]')) input.checked = input.value === feed.filter;
    byId('sched-refresh').classList.toggle('is-loading', feed.loading === 'all');
    byId('sched-refresh').disabled = Boolean(feed.loading);
    list.setAttribute('aria-busy', String(Boolean(feed.loading)));
    list.hidden = !groups.length;
    byId('sched-empty').hidden = groups.length > 0;
    if (!groups.length) {
      const demo = this.app.getBuffer().isDemo;
      const emptyTitles = demo
        ? { all: 'No Demo posts yet', upcoming: 'Nothing scheduled in Demo mode', published: 'Nothing published yet' }
        : { all: 'No posts in Buffer', upcoming: 'Nothing scheduled in Buffer', published: 'Nothing published yet' };
      const pending = feed.loading || (!feed.loaded && !feed.error);
      byId('sched-empty-title').textContent = pending ? (this.app.getBuffer().isDemo ? 'Loading Demo posts…' : 'Loading posts from Buffer…') : feed.error ? 'Could not load posts from Buffer' : emptyTitles[feed.filter];
      byId('sched-empty-text').textContent = pending ? '' : feed.error
        ? [feed.error.message, feed.error.hint].filter(Boolean).join(' ')
        : demo ? 'Posts you schedule in Demo mode appear here. They are simulated and kept in this tab only.'
          : 'Posts scheduled here or in Buffer appear in this list, read directly from Buffer. Until they are published, you can edit or delete them.';
    }
    const footer = byId('sched-footer');
    footer.hidden = !feed.loaded && !groups.length;
    byId('sched-source').textContent = feed.error && groups.length
      ? `Could not load more: ${feed.error.message}`
      : feed.loaded ? `${feed.loaded.demo ? 'Simulated Demo posts, kept in this tab only' : 'Read from Buffer'} · ${TimeZoneUtil.formatTime(new Date(feed.loaded.at), undefined)}` : '';
    const more = byId('sched-more');
    more.hidden = !feed.next;
    more.disabled = Boolean(feed.loading);
    more.classList.toggle('is-loading', feed.loading === 'more');

    // Keyed render: a row is rebuilt only when what it shows changed, so thumbnails do not reload.
    const zone = this.settings.timezone;
    // Highlighting every row says nothing; the new posts stand out only among older ones.
    this.allNew = groups.length > 0 && groups.every((g) => g.posts.some((p) => this.highlight.has(p.postId)));
    const rows = new Map();
    groups.forEach((group, index) => {
      const view = this.rowView(group, zone);
      const key = JSON.stringify(view);
      const id = group.posts[0].postId;
      let row = this.rows.get(id);
      if (!row || row.key !== key) row = { key, node: this.createRow(view) };
      rows.set(id, row);
      if (list.children[index] !== row.node) list.insertBefore(row.node, list.children[index] || null);
    });
    for (const [id, row] of this.rows) if (rows.get(id) !== row) row.node.remove();
    this.rows = rows;
  }

  /** @param {import('../services/buffer.js').BufferPost} post @returns {[string, string]} the status chip's label and tone */
  statusChip(post) {
    const [label, tone] = STATUS_CHIPS[post.status] || [post.status || 'Unknown', 'neutral'];
    return this.feed.loaded?.demo && post.status === 'scheduled' ? ['Scheduled · Demo', tone] : [label, tone];
  }

  /** Everything a posts-list row shows, as plain data. */
  rowView(group, zone) {
    const first = group.posts[0];
    const when = first.sentAt || first.dueAt;
    const { caption, tags } = splitPostText(first.text);
    const statuses = new Set(group.posts.map((p) => p.status));
    const copies = group.posts.map((post) => {
      const meta = serviceMeta(post.service);
      const [label, tone] = this.statusChip(post);
      return {
        postId: post.postId, color: meta.color, service: meta.label, name: post.channelName || meta.label, label, tone,
        error: post.error, externalLink: post.externalLink, busy: this.feed.busy.has(post.postId), changeable: BufferPostsFeed.canChange(post)
      };
    });
    const [label, tone] = this.statusChip(first);
    // Which gallery image the post came from, so the owner need not match thumbnails by eye.
    const items = this.store.all();
    const owner = items.findIndex((i) => (i.result?.postIds || []).some((id) => group.posts.some((p) => p.postId === id)));
    return {
      source: owner === -1 ? '' : `#${owner + 1} · ${items[owner].name}`,
      postIds: group.posts.map((p) => p.postId), caption, tags, thumbnail: group.posts.find((p) => p.thumbnail)?.thumbnail || null,
      when: when ? TimeZoneUtil.format(new Date(when), zone) : '', relative: when ? relativeTime(new Date(when)) : '',
      label: statuses.size === 1 ? label : '', tone, copies, isNew: !this.allNew && group.posts.some((p) => this.highlight.has(p.postId)),
      busy: copies.some((c) => c.busy), changeable: copies.some((c) => c.changeable)
    };
  }

  /** @param {ReturnType<PostsView['rowView']>} view @returns {HTMLElement} */
  createRow(view) {
    const { busy, changeable, copies } = view;
    const id = view.postIds[0];
    const several = copies.length > 1;
    const where = copies.map((c) => c.name).join(' and ');
    // Each row's buttons say which post they act on, so a list of buttons can tell the rows apart.
    const name = view.source ? `post ${view.source}` : `"${truncate((view.caption || 'post without text').replace(/\s+/g, ' '), 40)}"`;
    return el('li', { class: `sched-item${view.isNew ? ' is-new' : ''}` }, [
      view.thumbnail
        ? el('img', { class: 'sched-thumb', src: view.thumbnail, alt: '', loading: 'lazy', onerror: (e) => e.target.replaceWith(el('div', { class: 'sched-thumb', 'aria-hidden': 'true' })) })
        : el('div', { class: 'sched-thumb', 'aria-hidden': 'true' }),
      el('div', { class: 'sched-actions' }, [
        changeable ? el('button', { type: 'button', class: `btn btn-ghost btn-icon btn-sm${busy ? ' is-loading' : ''}`, disabled: busy, 'aria-label': `Edit ${name} on ${where}`, title: several ? 'Edit post (every channel)' : 'Edit post', dataset: { action: 'edit', id } }, [icon('edit')]) : null,
        changeable ? el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm', disabled: busy, 'aria-label': `Delete ${name} from ${where}`, title: several ? 'Delete post (every channel)' : 'Delete post', dataset: { action: 'delete-post', id } }, [icon('trash')]) : null
      ]),
      el('div', { class: 'sched-body' }, [
        scriptRuns(el('div', { class: 'sched-text' }), view.caption || '(no text)'),
        view.tags ? scriptRuns(el('div', { class: 'sched-tags', title: view.tags }), view.tags) : null,
        // The chip leads, so it sits in the same place on every row instead of wrapping on long ones.
        el('div', { class: 'sched-meta' }, [
          view.label ? el('span', { class: `chip chip-${view.tone}`, text: view.label }) : null,
          view.source ? el('span', { class: 'sched-source', text: view.source }) : null,
          el('span', {}, [icon('calendar'), view.when || 'No publish time']),
          view.relative ? el('span', {}, [icon('clock'), view.relative]) : null
        ]),
        el('ul', { class: 'list-reset sched-channels', 'aria-label': 'Channels' }, copies.map((copy) => el('li', { class: 'sched-channel' }, [
          colorDot(copy.color),
          el('span', { class: 'sched-channel-name', text: `${copy.name} · ${copy.service}`, title: `Post ID: ${copy.postId}` }),
          view.label ? null : el('span', { class: `chip chip-${copy.tone}`, text: copy.label }),
          copy.externalLink ? el('a', { class: 'btn btn-ghost btn-icon btn-xs', href: copy.externalLink, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Open the published ${copy.name} post`, title: 'Open published post' }, [icon('link')]) : null,
          several && copy.changeable ? el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-xs', disabled: copy.busy, 'aria-label': `Delete the ${copy.service} copy of ${name}`, title: `Delete the ${copy.service} copy only`, dataset: { action: 'delete-one', id: copy.postId } }, [icon('trash')]) : null,
          copy.error ? el('span', { class: 'error-text', text: copy.error }) : null
        ])))
      ])
    ]);
  }

  /** @param {string} postId @returns {import('../services/buffer.js').BufferPost[]} the post's copies on every channel */
  groupOf(postId) {
    return groupPosts(this.feed.posts).find((g) => g.posts.some((p) => p.postId === postId))?.posts || [];
  }

  /**
   * After "View in Scheduled posts": the upcoming list, soonest first, with the posts just created highlighted.
   * @param {string[]} postIds
   */
  showNew(postIds) {
    this.highlight = new Set(postIds);
    // The whole batch at once: the list drops its scroll box until the owner refreshes or changes the filter.
    byId('sched-list').classList.add('is-expanded');
    if (this.feed.filter !== 'upcoming') this.feed.setFilter('upcoming');
    else this.render();
  }

  bind() {
    byId('sched-list').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      const post = button && this.feed.get(button.dataset.id);
      if (!post) return;
      if (button.dataset.action === 'edit') this.edit(this.groupOf(post.postId));
      else if (button.dataset.action === 'delete-post') this.deletePosts(this.groupOf(post.postId), true);
      else if (button.dataset.action === 'delete-one') this.deletePosts([post], false);
    });
    for (const input of document.querySelectorAll('input[name="sched-filter"]')) {
      input.addEventListener('change', () => { this.highlight.clear(); byId('sched-list').classList.remove('is-expanded'); this.feed.setFilter(input.value); });
    }
    byId('sched-refresh').addEventListener('click', () => this.feed.load());
    byId('sched-more').addEventListener('click', () => this.feed.load({ more: true }));

    const dialog = byId('post-dialog');
    byId('post-form').addEventListener('submit', (event) => { event.preventDefault(); this.saveDialog(); });
    byId('post-cancel').addEventListener('click', () => { if (!this.postEdit?.saving) dialog.close(); });
    dialog.addEventListener('cancel', (event) => { if (this.postEdit?.saving) event.preventDefault(); });
    dialog.addEventListener('close', () => { this.postEdit = null; });
    byId('post-text').addEventListener('input', () => this.updateTextCount());
    byId('post-due').addEventListener('input', () => this.updateDueHint());
  }

  /**
   * One editor per post: a post from this batch opens in the post editor, with the same caption and hashtag
   * fields it was written in; a post Poster did not schedule from this gallery opens in the dialog.
   * @param {import('../services/buffer.js').BufferPost[]} copies
   */
  edit(copies) {
    const owner = this.ownerOf(copies);
    if (owner) this.ui.editor.editScheduled(owner.id);
    else this.openDialog(copies);
  }

  /** @returns {object|null} the gallery image these copies were scheduled from, while it still counts as scheduled */
  ownerOf(copies) {
    const ids = new Set(copies.map((p) => p.postId));
    return this.store.all().find((i) => countedResult(i, this.settings.mode) && (i.result.postIds || []).some((id) => ids.has(id))) || null;
  }

  /** The dialog's publish time, written out the way the editor shows it. */
  updateDueHint() {
    const zone = this.postEdit?.zone || this.settings.timezone;
    const check = this.validation.validateSchedule(byId('post-due').value, { ...this.settings, timezone: zone });
    byId('post-due-hint').textContent = check.valid ? `${TimeZoneUtil.format(check.date, zone)} · ${relativeTime(check.date)}` : '';
  }

  /** @returns {string} where the change happens, said plainly in Demo mode */
  get place() {
    return this.app.getBuffer().isDemo ? 'Demo mode (not in Buffer)' : 'Buffer';
  }

  /**
   * Opens the editor for a post as it was last read from Buffer; a change applies to every channel copy
   * that can still change. The server checks again that each is not published before changing it.
   * @param {import('../services/buffer.js').BufferPost[]} copies
   */
  openDialog(copies) {
    const editable = copies.filter((p) => BufferPostsFeed.canChange(p) && !this.feed.busy.has(p.postId));
    if (byId('post-dialog').open || !editable.length) return;
    const post = editable[0];
    const zone = this.settings.timezone;
    const dueLocal = post.dueAt ? TimeZoneUtil.toLocalInput(new Date(post.dueAt), zone) : '';
    this.postEdit = {
      postIds: editable.map((p) => p.postId), text: post.text, dueLocal, zone, saving: false,
      toX: editable.some((p) => serviceMeta(p.service).label === 'X')
    };
    const channels = editable.map((p) => `${p.channelName || serviceMeta(p.service).label} (${serviceMeta(p.service).label})`).join(' and ');
    const demo = this.app.getBuffer().isDemo ? 'Demo mode, not in Buffer. ' : '';
    byId('post-dialog-sub').textContent = `${demo}On ${channels}${editable.length > 1 ? '; changes apply to every copy' : ''}. If it is published before you save, it is left as published.`;
    byId('post-text').value = post.text;
    byId('post-due').value = dueLocal;
    byId('post-due').min = TimeZoneUtil.toLocalInput(new Date(), zone);
    byId('post-due-zone').textContent = zone;
    this.updateTextCount();
    this.updateDueHint();
    for (const field of ['post-text', 'post-due']) setFieldError(field, null);
    byId('post-dialog-error').hidden = true;
    this.setSaving(false);
    byId('post-dialog').showModal();
    byId('post-text').focus();
  }

  updateTextCount() {
    const text = byId('post-text').value;
    const length = charCount(text);
    const counter = byId('post-text-count');
    const overX = length > 280 && this.postEdit?.toX;
    counter.textContent = `${length} characters${overX ? ' · over X’s 280 limit' : ''}`;
    counter.classList.toggle('is-over', Boolean(overX));
    applyScriptLang(byId('post-text'), text);
  }

  /**
   * The hashtags in an edited post must follow the same rules as in the editor: at most the maximum, no duplicates.
   * @param {string} text @returns {string|null} error message or null
   */
  checkHashtags(text) {
    const tags = (text.match(/#[\p{L}\p{M}\p{N}_\p{Cf}]+/gu) || []).map((t) => this.validation.normalizeHashtag(t)).filter(Boolean);
    if (tags.length > LIMITS.maxHashtags) return `This post has ${tags.length} hashtags; the maximum is ${LIMITS.maxHashtags}.`;
    const lower = tags.map((t) => t.toLowerCase());
    const dupes = [...new Set(lower.filter((t, i) => lower.indexOf(t) !== i))];
    return dupes.length ? `Duplicate hashtags: ${dupes.join(', ')}. Each hashtag must be unique.` : null;
  }

  /** @param {boolean} saving */
  setSaving(saving) {
    if (this.postEdit) this.postEdit.saving = saving;
    byId('post-save').classList.toggle('is-loading', saving);
    for (const id of ['post-text', 'post-due', 'post-save', 'post-cancel']) byId(id).disabled = saving;
  }

  /** Validates the dialog, sends only what changed to every copy, and keeps the dialog open on errors. */
  async saveDialog() {
    const edit = this.postEdit;
    if (!edit || edit.saving) return;
    const text = byId('post-text').value;
    const dueLocal = byId('post-due').value;
    const changes = {};
    const errors = {};
    if (text !== edit.text) {
      const tagError = this.checkHashtags(text);
      if (!text.trim()) errors['post-text'] = 'The post text cannot be empty.';
      else if (text.length > LIMITS.maxPostChars) errors['post-text'] = `Keep the post under ${LIMITS.maxPostChars.toLocaleString()} characters.`;
      else if (tagError) errors['post-text'] = tagError;
      else changes.text = text;
    }
    if (dueLocal !== edit.dueLocal) {
      const check = this.validation.validateSchedule(dueLocal, { ...this.settings, timezone: edit.zone });
      if (check.valid) changes.dueAt = check.date;
      else errors['post-due'] = check.error;
    }
    for (const field of ['post-text', 'post-due']) setFieldError(field, errors[field] || null);
    byId('post-dialog-error').hidden = true;
    if (Object.keys(errors).length) {
      byId(Object.keys(errors)[0]).focus();
      return;
    }
    if (!Object.keys(changes).length) {
      byId('post-dialog').close();
      showToast(this.place === 'Buffer' ? 'Nothing changed, so Buffer was not updated.' : 'Nothing changed.', 'info');
      return;
    }
    this.setSaving(true);
    const failed = [];
    for (const postId of edit.postIds) {
      try {
        await this.feed.edit(postId, changes);
      } catch (error) {
        failed.push([error.message, error.hint].filter(Boolean).join(' '));
      }
    }
    this.setSaving(false);
    if (failed.length) {
      const box = byId('post-dialog-error');
      box.textContent = failed.length < edit.postIds.length
        ? `Updated ${edit.postIds.length - failed.length} of ${edit.postIds.length} copies. ${failed[0]}`
        : failed[0];
      box.hidden = false;
      return;
    }
    byId('post-dialog').close();
    showToast(`The post was updated in ${this.place}.`, 'success');
  }

  /**
   * Deletes a post's copies (all of them, or one channel's) after one confirmation that names which.
   * @param {import('../services/buffer.js').BufferPost[]} copies @param {boolean} whole the whole post, or one copy
   */
  async deletePosts(copies, whole) {
    const targets = copies.filter((p) => BufferPostsFeed.canChange(p) && !this.feed.busy.has(p.postId));
    if (!targets.length) return;
    const where = targets.map((p) => `${p.channelName || serviceMeta(p.service).label} (${serviceMeta(p.service).label})`).join(' and ');
    const others = whole ? [] : this.groupOf(targets[0].postId).filter((p) => !targets.includes(p));
    const rowIndex = [...byId('sched-list').children].findIndex((li) => li.contains(document.activeElement));
    const { caption } = splitPostText(targets[0].text);
    const ok = await this.ui.confirm({
      title: !whole ? `Delete the ${serviceMeta(targets[0].service).label} copy?` : this.place === 'Buffer' ? 'Delete this post from Buffer?' : 'Delete this Demo post?',
      message: `"${truncate(caption.replace(/\s+/g, ' '), 80)}" on ${where} will be deleted and will not be published. This cannot be undone.`,
      confirmLabel: !whole ? `Delete ${serviceMeta(targets[0].service).label} copy` : targets.length > 1 ? `Delete post (${pluralize(targets.length, 'channel')})` : 'Delete post',
      danger: true
    });
    if (!ok) return;
    const failed = [];
    for (const post of targets) {
      try {
        await this.feed.remove(post.postId);
      } catch (error) {
        failed.push(error.message);
      }
    }
    if (failed.length) showToast(failed[0], 'error', { title: failed.length < targets.length ? 'Some copies were not deleted' : 'Could not delete the post' });
    else if (!whole) {
      const kept = others.map((p) => serviceMeta(p.service).label).join(' and ');
      showToast(`${serviceMeta(targets[0].service).label} copy deleted${kept ? `; ${kept} ${others.length === 1 ? 'copy' : 'copies'} still scheduled` : ''}${this.place === 'Buffer' ? '' : ' (Demo mode, not in Buffer)'}.`, 'success');
    } else showToast(this.place === 'Buffer' ? 'The post was deleted from Buffer.' : 'The post was deleted in Demo mode (not in Buffer).', 'success');
    // Keep the keyboard in the list: the row now in the deleted one's place, or the heading when none is left.
    if (rowIndex !== -1) {
      const rows = byId('sched-list').children;
      const row = rows[Math.min(rowIndex, rows.length - 1)];
      (row?.querySelector('[data-action=edit], [data-action=delete-post]') || byId('sched-title')).focus();
    }
  }
}
