/** The Scheduled posts card and the Edit scheduled post dialog. */
import { showToast } from '../app.js';
import { LIMITS } from '../config.js';
import { BufferPostsFeed } from '../feed.js';
import { serviceMeta } from '../services/buffer.js';
import { TimeZoneUtil } from '../time.js';
import { charCount, el, icon, relativeTime, truncate } from '../util.js';
import { byId, colorDot, setFieldError } from './dom.js';
import { View } from './view.js';

const STATUS_CHIPS = Object.freeze({
  scheduled: ['Scheduled', 'success'], needs_approval: ['Needs approval', 'warning'], sending: ['Publishing', 'info'],
  sent: ['Published', 'neutral'], error: ['Failed', 'danger'], draft: ['Draft', 'neutral']
});

export class PostsView extends View {
  constructor(ui) {
    super(ui);
    /** @type {{postId: string, text: string, dueLocal: string, zone: string, saving: boolean}|null} the post being edited */
    this.postEdit = null;
    /** @type {Map<string, {key: string, node: HTMLElement}>} list rows by post id, rebuilt only when they change */
    this.rows = new Map();
  }

  /** The Scheduled posts card: posts as last read from Buffer. */
  render() {
    const feed = this.feed;
    const { posts } = feed;
    const list = byId('sched-list');
    byId('sched-count').textContent = `${posts.length}${feed.next ? '+' : ''}`;
    for (const input of document.querySelectorAll('input[name="sched-filter"]')) input.checked = input.value === feed.filter;
    byId('sched-refresh').classList.toggle('is-loading', feed.loading === 'all');
    byId('sched-refresh').disabled = Boolean(feed.loading);
    list.setAttribute('aria-busy', String(Boolean(feed.loading)));
    list.hidden = !posts.length;
    byId('sched-empty').hidden = posts.length > 0;
    if (!posts.length) {
      const emptyTitles = { all: 'No posts in Buffer', upcoming: 'Nothing scheduled in Buffer', published: 'Nothing published yet' };
      const pending = feed.loading || (!feed.loaded && !feed.error);
      byId('sched-empty-title').textContent = pending ? 'Loading posts from Buffer…' : feed.error ? 'Could not load posts from Buffer' : emptyTitles[feed.filter];
      byId('sched-empty-text').textContent = pending ? '' : feed.error
        ? [feed.error.message, feed.error.hint].filter(Boolean).join(' ')
        : 'Posts scheduled here or in Buffer appear in this list, read directly from Buffer. Until they are published, you can edit or delete them.';
    }
    const footer = byId('sched-footer');
    footer.hidden = !feed.loaded && !posts.length;
    byId('sched-source').textContent = feed.error && posts.length
      ? `Could not load more: ${feed.error.message}`
      : feed.loaded ? `${feed.loaded.demo ? 'Simulated Demo posts, kept in this tab only' : 'Read from Buffer'} · ${new Date(feed.loaded.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : '';
    const more = byId('sched-more');
    more.hidden = !feed.next;
    more.disabled = Boolean(feed.loading);
    more.classList.toggle('is-loading', feed.loading === 'more');

    // Keyed render: a row is rebuilt only when what it shows changed, so thumbnails do not reload.
    const zone = this.settings.timezone;
    const rows = new Map();
    posts.forEach((post, index) => {
      const view = this.rowView(post, zone);
      const key = JSON.stringify(view);
      let row = this.rows.get(post.postId);
      if (!row || row.key !== key) row = { key, node: this.createRow(view) };
      rows.set(post.postId, row);
      if (list.children[index] !== row.node) list.insertBefore(row.node, list.children[index] || null);
    });
    for (const [id, row] of this.rows) if (rows.get(id) !== row) row.node.remove();
    this.rows = rows;
  }

  /** Everything a posts-list row shows, as plain data. */
  rowView(post, zone) {
    const when = post.sentAt || post.dueAt;
    const [label, tone] = STATUS_CHIPS[post.status] || [post.status || 'Unknown', 'neutral'];
    const meta = serviceMeta(post.service);
    return {
      postId: post.postId, text: post.text, thumbnail: post.thumbnail, error: post.error, externalLink: post.externalLink,
      when: when ? TimeZoneUtil.format(new Date(when), zone) : '', relative: when ? relativeTime(new Date(when)) : '',
      label, tone, color: meta.color, service: meta.label, name: post.channelName || meta.label,
      demo: Boolean(this.feed.loaded?.demo), busy: this.feed.busy.has(post.postId), changeable: BufferPostsFeed.canChange(post)
    };
  }

  /** @param {ReturnType<PostsView['rowView']>} view @returns {HTMLElement} */
  createRow(view) {
    const { name, busy, changeable } = view;
    return el('li', { class: 'sched-item' }, [
      view.thumbnail
        ? el('img', { class: 'sched-thumb', src: view.thumbnail, alt: '', loading: 'lazy', onerror: (e) => e.target.replaceWith(el('div', { class: 'sched-thumb', 'aria-hidden': 'true' })) })
        : el('div', { class: 'sched-thumb', 'aria-hidden': 'true' }),
      el('div', { style: 'min-width:0' }, [
        el('div', { class: 'sched-text', text: view.text || '(no text)' }),
        el('div', { class: 'sched-meta' }, [
          el('span', {}, [icon('calendar'), view.when || 'No publish time']),
          view.relative ? el('span', {}, [icon('clock'), view.relative]) : null,
          el('span', {}, [colorDot(view.color), `${name} · ${view.service}`]),
          view.demo ? el('span', { class: 'chip chip-accent', text: 'Demo' }) : null,
          el('span', { class: `chip chip-${view.tone}`, text: view.label })
        ]),
        el('div', { class: 'sched-ids', text: `Post ID: ${view.postId}` }),
        view.error ? el('div', { class: 'error-text', text: view.error }) : null
      ]),
      el('div', { class: 'sched-actions' }, [
        changeable ? el('button', { type: 'button', class: `btn btn-ghost btn-icon btn-sm${busy ? ' is-loading' : ''}`, disabled: busy, 'aria-label': `Edit this ${name} post in Buffer`, title: 'Edit post', dataset: { action: 'edit', id: view.postId } }, [icon('edit')]) : null,
        changeable ? el('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm', disabled: busy, 'aria-label': `Delete this ${name} post from Buffer`, title: 'Delete post from Buffer', dataset: { action: 'delete-post', id: view.postId } }, [icon('calendar-x')]) : null,
        view.externalLink ? el('a', { class: 'btn btn-ghost btn-icon btn-sm', href: view.externalLink, target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Open the published ${name} post`, title: 'Open published post' }, [icon('link')]) : null
      ])
    ]);
  }

  bind() {
    byId('sched-list').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-action]');
      const post = button && this.feed.get(button.dataset.id);
      if (!post) return;
      if (button.dataset.action === 'edit') this.openDialog(post);
      else if (button.dataset.action === 'delete-post') this.deletePost(post);
    });
    for (const input of document.querySelectorAll('input[name="sched-filter"]')) {
      input.addEventListener('change', () => this.feed.setFilter(input.value));
    }
    byId('sched-refresh').addEventListener('click', () => this.feed.load());
    byId('sched-more').addEventListener('click', () => this.feed.load({ more: true }));

    const dialog = byId('post-dialog');
    byId('post-form').addEventListener('submit', (event) => { event.preventDefault(); this.saveDialog(); });
    byId('post-cancel').addEventListener('click', () => { if (!this.postEdit?.saving) dialog.close(); });
    dialog.addEventListener('cancel', (event) => { if (this.postEdit?.saving) event.preventDefault(); });
    dialog.addEventListener('close', () => { this.postEdit = null; });
    byId('post-text').addEventListener('input', () => this.updateTextCount());
  }

  /**
   * Opens the editor for a post as it was last read from Buffer. The server
   * checks again that it is not published before changing it.
   * @param {import('../services/buffer.js').BufferPost} post
   */
  openDialog(post) {
    if (byId('post-dialog').open || this.feed.busy.has(post.postId) || !BufferPostsFeed.canChange(post)) return;
    const zone = this.settings.timezone;
    const dueLocal = post.dueAt ? TimeZoneUtil.toLocalInput(new Date(post.dueAt), zone) : '';
    this.postEdit = { postId: post.postId, text: post.text, dueLocal, zone, saving: false };
    const channel = `${post.channelName || serviceMeta(post.service).label} (${serviceMeta(post.service).label})`;
    byId('post-dialog-sub').textContent = `On ${channel}. If Buffer publishes it before you save, it is left as published.`;
    byId('post-text').value = post.text;
    byId('post-due').value = dueLocal;
    byId('post-due').min = TimeZoneUtil.toLocalInput(new Date(), zone);
    byId('post-due-zone').textContent = zone;
    this.updateTextCount();
    for (const field of ['post-text', 'post-due']) setFieldError(field, null);
    byId('post-dialog-error').hidden = true;
    this.setSaving(false);
    byId('post-dialog').showModal();
    byId('post-text').focus();
  }

  updateTextCount() {
    const length = charCount(byId('post-text').value);
    const counter = byId('post-text-count');
    counter.textContent = `${length} characters${length > 280 ? ' · over X’s 280 limit' : ''}`;
  }

  /** @param {boolean} saving */
  setSaving(saving) {
    if (this.postEdit) this.postEdit.saving = saving;
    byId('post-save').classList.toggle('is-loading', saving);
    for (const id of ['post-text', 'post-due', 'post-save', 'post-cancel']) byId(id).disabled = saving;
  }

  /** Validates the dialog, sends only what changed, and keeps the dialog open on errors. */
  async saveDialog() {
    const edit = this.postEdit;
    if (!edit || edit.saving) return;
    const text = byId('post-text').value;
    const dueLocal = byId('post-due').value;
    const changes = {};
    const errors = {};
    if (text !== edit.text) {
      if (!text.trim()) errors['post-text'] = 'The post text cannot be empty.';
      else if (text.length > LIMITS.maxPostChars) errors['post-text'] = `Keep the post under ${LIMITS.maxPostChars.toLocaleString()} characters.`;
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
      showToast('Nothing changed, so Buffer was not updated.', 'info');
      return;
    }
    this.setSaving(true);
    try {
      await this.feed.edit(edit.postId, changes);
      this.setSaving(false);
      byId('post-dialog').close();
      showToast('The post was updated in Buffer.', 'success');
    } catch (error) {
      this.setSaving(false);
      const box = byId('post-dialog-error');
      box.textContent = [error.message, error.hint].filter(Boolean).join(' ');
      box.hidden = false;
    }
  }

  /**
   * Deletes a post from Buffer after confirmation.
   * @param {import('../services/buffer.js').BufferPost} post
   */
  async deletePost(post) {
    if (this.feed.busy.has(post.postId) || !BufferPostsFeed.canChange(post)) return;
    const channel = `${post.channelName || serviceMeta(post.service).label} (${serviceMeta(post.service).label})`;
    const ok = await this.ui.confirm({
      title: 'Delete this post from Buffer?',
      message: `"${truncate(post.text.replace(/\s+/g, ' '), 80)}" on ${channel} will be deleted from Buffer and will not be published. This cannot be undone.`,
      confirmLabel: 'Delete post',
      danger: true
    });
    if (!ok) return;
    try {
      await this.feed.remove(post.postId);
      showToast('The post was deleted from Buffer.', 'success');
    } catch (error) {
      showToast(error.message, 'error', { title: 'Could not delete the post' });
    }
  }
}
