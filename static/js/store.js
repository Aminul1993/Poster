/** ImageStore: gallery state, ordering, selection and draft persistence. */
import { LIMITS, STORAGE_KEYS, TIMING } from './config.js';
import { canvasToBlob, computeImageStats, decodeImage, drawScaled } from './image.js';
import { clamp, debounce, isPlainObject, uid } from './util.js';

/** @returns {object} an empty content object */
export function emptyContent() {
  return { description: '', alternative_description: '', hashtags: [], category: '', engagement_score: 50 };
}

/** @param {object} item @returns {boolean} whether the item has a caption */
export function hasContent(item) {
  return Boolean(item?.content && String(item.content.description || '').trim());
}

/**
 * Derived display status of an image.
 * @param {object} item
 * @returns {{key: string, label: string, chip: string}}
 */
export function itemStatus(item) {
  if (item.busy === 'generating') return { key: 'generating', label: 'Generating…', chip: 'info' };
  if (item.busy === 'scheduling') return { key: 'scheduling', label: 'Scheduling…', chip: 'info' };
  if (item.busy === 'queued') return { key: 'queued', label: 'Queued', chip: 'neutral' };
  if (item.error) return { key: 'error', label: 'Failed', chip: 'danger' };
  if (item.result?.partial) return { key: 'partial', label: 'Partly scheduled', chip: 'warning' };
  if (item.result) return { key: 'scheduled', label: 'Scheduled', chip: 'success' };
  if (hasContent(item)) return { key: 'ready', label: 'Ready', chip: 'accent' };
  return { key: 'new', label: 'No content', chip: 'neutral' };
}

/**
 * Holds the uploaded images. Emits "change" events with {reason, ids}.
 * Generated content, schedule times and media URLs are saved per image
 * fingerprint, so re-uploading the same file restores its draft.
 */
export class ImageStore extends EventTarget {
  /** @param {{storage: StorageService}} deps */
  constructor({ storage }) {
    super();
    this.storage = storage;
    /** @type {object[]} */
    this.items = [];
    this.activeId = null;
    const drafts = storage.get(STORAGE_KEYS.drafts, {});
    this.drafts = isPlainObject(drafts) ? drafts : {};
    this.persistDrafts = debounce(() => this.#writeDrafts(), TIMING.draftPersistMs);
  }

  get count() { return this.items.length; }
  get active() { return this.get(this.activeId); }
  all() { return this.items; }
  get(id) { return this.items.find((item) => item.id === id) || null; }
  indexOf(id) { return this.items.findIndex((item) => item.id === id); }
  selected() { return this.items.filter((item) => item.selected); }
  fingerprints() { return new Set(this.items.map((item) => item.fingerprint)); }

  /**
   * Decodes the file, builds the thumbnails and colour stats, and restores any
   * saved draft. Throws when the image cannot be decoded. The page shows the
   * thumbnails only; the original file is kept for analysis and upload.
   * @param {File} file @param {string} fingerprint
   * @returns {Promise<object>} image item (not yet added)
   */
  async createItem(file, fingerprint) {
    const source = await decodeImage(file);
    let preview;
    const width = source.naturalWidth || source.width;
    const height = source.naturalHeight || source.height;
    try {
      preview = drawScaled(source, LIMITS.previewMaxEdge);
    } finally {
      if (typeof source.close === 'function') source.close();
    }
    const thumb = drawScaled(preview, LIMITS.thumbMaxEdge, { readable: true });
    const thumbDataUrl = thumb.toDataURL('image/jpeg', 0.72);
    const stats = computeImageStats(thumb);
    const previewBlob = await canvasToBlob(preview, 'image/jpeg', 0.85);
    const draft = isPlainObject(this.drafts[fingerprint]) ? this.drafts[fingerprint] : null;
    const content = draft && isPlainObject(draft.content) ? { ...emptyContent(), ...draft.content, hashtags: Array.isArray(draft.content.hashtags) ? draft.content.hashtags.filter((t) => typeof t === 'string') : [] } : null;
    return {
      id: uid('img'),
      file,
      name: file.name || 'image',
      size: file.size,
      type: file.type,
      fingerprint,
      previewUrl: previewBlob ? URL.createObjectURL(previewBlob) : thumbDataUrl,
      width,
      height,
      thumbDataUrl,
      stats,
      selected: false,
      busy: null,
      error: null,
      errorHint: '',
      errorPhase: null,
      showErrors: false,
      content,
      useAlternative: Boolean(draft?.useAlternative),
      scheduleLocal: typeof draft?.scheduleLocal === 'string' ? draft.scheduleLocal : '',
      mediaUrl: typeof draft?.mediaUrl === 'string' ? draft.mediaUrl : '',
      uploadedUrl: typeof draft?.uploadedUrl === 'string' ? draft.uploadedUrl : '',
      uploadedThumbUrl: typeof draft?.uploadedThumbUrl === 'string' ? draft.uploadedThumbUrl : '',
      result: isPlainObject(draft?.result) ? draft.result : null,
      generatedAt: draft?.generatedAt || null,
      restored: Boolean(draft && (content || draft.result)),
      analysisBase64: null
    };
  }

  /** @param {object[]} items */
  add(items) {
    if (!items.length) return;
    this.items.push(...items);
    if (!this.active) this.activeId = items[0].id;
    this.#emit('add', items.map((i) => i.id));
  }

  /** @param {string} id */
  remove(id) {
    const index = this.indexOf(id);
    if (index === -1) return;
    const [item] = this.items.splice(index, 1);
    URL.revokeObjectURL(item.previewUrl);
    if (this.activeId === id) this.activeId = (this.items[index] || this.items[index - 1] || null)?.id ?? null;
    this.#emit('remove', [id]);
  }

  /** Removes every image (drafts are kept). */
  clear() {
    for (const item of this.items) URL.revokeObjectURL(item.previewUrl);
    const ids = this.items.map((i) => i.id);
    this.items = [];
    this.activeId = null;
    this.#emit('clear', ids);
  }

  /** @param {string} id @param {number} delta */
  move(id, delta) {
    const from = this.indexOf(id);
    const to = from + delta;
    if (from === -1 || to < 0 || to >= this.items.length) return false;
    const [item] = this.items.splice(from, 1);
    this.items.splice(to, 0, item);
    this.#emit('reorder', [id]);
    return true;
  }

  /**
   * Moves an item so it ends up before the item currently at `targetIndex`.
   * @param {string} id @param {number} targetIndex
   */
  moveTo(id, targetIndex) {
    const from = this.indexOf(id);
    if (from === -1) return false;
    let to = clamp(targetIndex, 0, this.items.length);
    if (from < to) to -= 1;
    if (to === from) return false;
    const [item] = this.items.splice(from, 1);
    this.items.splice(to, 0, item);
    this.#emit('reorder', [id]);
    return true;
  }

  /** @param {string|null} id */
  setActive(id) {
    if (id !== null && !this.get(id)) return;
    if (this.activeId === id) return;
    this.activeId = id;
    this.#emit('active', id ? [id] : []);
  }

  /** @param {string} id @param {boolean} selected */
  setSelected(id, selected) {
    const item = this.get(id);
    if (!item || item.selected === selected) return;
    item.selected = selected;
    this.#emit('select', [id]);
  }

  /** @param {boolean} selected */
  selectAll(selected) {
    for (const item of this.items) item.selected = selected;
    this.#emit('select', this.items.map((i) => i.id));
  }

  /**
   * Applies a patch to an item.
   * @param {string} id
   * @param {object} patch
   * @param {{reason?: string, persist?: boolean}} [options] persist saves the draft
   */
  update(id, patch, { reason = 'update', persist = true } = {}) {
    const item = this.get(id);
    if (!item) return null;
    Object.assign(item, patch);
    if (persist) this.saveDraft(item);
    this.#emit(reason, [id]);
    return item;
  }

  /** Saves the persistent parts of an item, keyed by its fingerprint. */
  saveDraft(item) {
    const worthSaving = item.content || item.scheduleLocal || item.mediaUrl || item.result || item.uploadedUrl;
    if (!worthSaving) {
      delete this.drafts[item.fingerprint];
    } else {
      this.drafts[item.fingerprint] = {
        name: item.name,
        content: item.content,
        useAlternative: item.useAlternative,
        scheduleLocal: item.scheduleLocal,
        mediaUrl: item.mediaUrl,
        uploadedUrl: item.uploadedUrl,
        uploadedThumbUrl: item.uploadedThumbUrl,
        result: item.result,
        generatedAt: item.generatedAt,
        updatedAt: Date.now()
      };
    }
    this.persistDrafts();
  }

  /**
   * Updates the schedule result of images whose Buffer posts changed, open in
   * the gallery or saved as drafts.
   * @param {string[]} postIds
   * @param {object|null} patch fields to change, or null when the posts were deleted (an image with
   *   none of its posts left can be scheduled again)
   */
  updateResultsForPosts(postIds, patch) {
    const matches = (result) => Array.isArray(result?.postIds) && result.postIds.some((id) => postIds.includes(id));
    const apply = (result) => {
      if (patch) return { ...result, ...patch };
      const keep = result.postIds.map((id) => !postIds.includes(id));
      const remaining = result.postIds.filter((_, i) => keep[i]);
      return remaining.length ? { ...result, postIds: remaining, channels: (result.channels || []).filter((_, i) => keep[i]) } : null;
    };
    for (const item of this.items) {
      if (matches(item.result)) this.update(item.id, { result: apply(item.result) }, { reason: 'status' });
    }
    for (const draft of Object.values(this.drafts)) {
      if (matches(draft?.result)) draft.result = apply(draft.result);
    }
    this.persistDrafts();
  }

  /** @returns {number} number of saved drafts */
  draftCount() {
    return Object.keys(this.drafts).length;
  }

  /** Deletes every saved draft (open images keep their current content). */
  clearDrafts() {
    this.drafts = {};
    this.storage.remove(STORAGE_KEYS.drafts);
  }

  #writeDrafts() {
    const entries = Object.entries(this.drafts).sort((a, b) => (b[1]?.updatedAt || 0) - (a[1]?.updatedAt || 0));
    if (entries.length > LIMITS.maxDrafts) this.drafts = Object.fromEntries(entries.slice(0, LIMITS.maxDrafts));
    this.storage.set(STORAGE_KEYS.drafts, this.drafts, {
      shrink: (drafts) => Object.fromEntries(Object.entries(drafts).sort((a, b) => (b[1]?.updatedAt || 0) - (a[1]?.updatedAt || 0)).slice(0, LIMITS.draftsWhenFull))
    });
  }

  #emit(reason, ids) {
    this.dispatchEvent(new CustomEvent('change', { detail: { reason, ids } }));
  }
}
