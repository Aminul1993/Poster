/** ValidationService: images, captions, hashtags, schedules, settings, imports. */
import { LIMITS, RULES, SERVER } from './config.js';
import { ApiError } from './errors.js';
import { SettingsModel } from './settings.js';
import { TimeZoneUtil } from './time.js';
import { charCount, clamp, formatBytes, isLocalHost, isPlainObject, pluralize, truncate } from './util.js';

export class ValidationService {
  /** @param {() => object} getSettings */
  constructor(getSettings) {
    this.getSettings = getSettings;
  }

  /* ---------------------------------------------------------------- images */

  /**
   * Validates an image file: type (by MIME, extension and magic bytes), size,
   * decodability is checked later when the thumbnail is built, and duplicates
   * by content hash.
   * @param {File} file
   * @param {{fingerprints?: Set<string>, count?: number}} [context]
   * @returns {Promise<{valid: boolean, errors: string[], fingerprint?: string, detectedType?: string}>}
   */
  async validateImage(file, context = {}) {
    const errors = [];
    if (!(file instanceof Blob)) return { valid: false, errors: ['This is not a file.'] };
    const name = file.name || 'image';
    const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
    const typeAllowed = LIMITS.allowedTypes.includes(file.type);
    const extAllowed = LIMITS.allowedExtensions.includes(ext);
    if (!typeAllowed && !extAllowed) {
      errors.push(`Unsupported file type (${file.type || (ext ? `.${ext}` : 'unknown')}). Use JPG, JPEG, PNG or WEBP.`);
    }
    if (file.size === 0) errors.push('The file is empty.');
    else if (file.size > LIMITS.maxImageBytes) errors.push(`The file is ${formatBytes(file.size)}; the limit is ${formatBytes(LIMITS.maxImageBytes)} per image.`);
    if (typeof context.count === 'number' && context.count >= LIMITS.maxImages) {
      errors.push(`The gallery is full (maximum ${LIMITS.maxImages} images). Remove an image first.`);
    }
    if (errors.length) return { valid: false, errors };

    const detectedType = await ValidationService.sniffImageType(file);
    if (!detectedType) {
      return { valid: false, errors: ['The file content is not a JPG, PNG or WEBP image (it may be renamed or corrupted).'] };
    }
    const fingerprint = await ValidationService.fingerprint(file);
    if (context.fingerprints?.has(fingerprint)) {
      return { valid: false, errors: ['Duplicate: this image is already in the gallery.'], fingerprint };
    }
    return { valid: true, errors: [], fingerprint, detectedType };
  }

  /**
   * Detects the real image format from the first bytes of the file.
   * @param {Blob} file @returns {Promise<string|null>} MIME type or null
   */
  static async sniffImageType(file) {
    try {
      const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
      if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'image/jpeg';
      if (bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47 &&
        bytes[4] === 0x0d && bytes[5] === 0x0a && bytes[6] === 0x1a && bytes[7] === 0x0a) return 'image/png';
      const ascii = (start, end) => String.fromCharCode(...bytes.slice(start, end));
      if (bytes.length >= 12 && ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP') return 'image/webp';
      return null;
    } catch {
      return null;
    }
  }

  /**
   * Content fingerprint used for duplicate detection: SHA-256 of the bytes
   * when Web Crypto is available, otherwise name + size + modified time.
   * @param {File} file @returns {Promise<string>}
   */
  static async fingerprint(file) {
    try {
      if (window.crypto?.subtle) {
        const digest = await window.crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        return `sha256:${Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')}`;
      }
    } catch { /* fall back below */ }
    let hash = 0x811c9dc5;
    const text = `${file.name}|${file.size}|${file.lastModified}`;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return `meta:${hash.toString(16)}:${file.size}`;
  }

  /* --------------------------------------------------------------- content */

  /**
   * @param {string} text @param {string} label @param {object} [settings]
   * @returns {string|null} error message or null when valid
   */
  validateCaption(text, label = 'Caption', settings = this.getSettings()) {
    const length = charCount(String(text ?? '').trim());
    if (!length) return `${label} is required.`;
    if (length < settings.descMin) return `${label} is too short: ${length} characters, minimum ${settings.descMin}.`;
    if (length > settings.descMax) return `${label} is too long: ${length} characters, maximum ${settings.descMax}.`;
    return null;
  }

  /**
   * Turns user or model input into a canonical "#tag": letters, combining marks (the vowel signs of
   * Bengali, Hindi, Tamil...), digits and _, plus the zero-width joiner and non-joiner inside a word, which
   * choose a conjunct's shape (র\u200d্য versus র্য) and so change how the word reads.
   * @param {string} raw @returns {string|null}
   */
  normalizeHashtag(raw) {
    const tag = String(raw ?? '').normalize('NFC').trim().replace(/^#+/, '').replace(/[^\p{L}\p{M}\p{N}_\u200c\u200d]/gu, '')
      .replace(/^[\u200c\u200d]+|[\u200c\u200d]+$/g, '');
    if (!tag || /^\d+$/.test(tag) || tag.length > 80) return null;
    return `#${tag}`;
  }

  /**
   * Normalises and de-duplicates a list (case-insensitive), keeping order.
   * @param {Array<string>|string} input @returns {string[]}
   */
  normalizeHashtagList(input) {
    const raw = Array.isArray(input) ? input : String(input ?? '').split(/[\s,;]+/);
    const seen = new Set();
    const out = [];
    for (const item of raw) {
      const tag = this.normalizeHashtag(item);
      if (tag && !seen.has(tag.toLowerCase())) {
        seen.add(tag.toLowerCase());
        out.push(tag);
      }
    }
    return out;
  }

  /**
   * @param {string[]} tags @returns {string|null} error message or null
   */
  validateHashtags(tags) {
    const list = Array.isArray(tags) ? tags : [];
    if (list.length > LIMITS.maxHashtags) return `Too many hashtags: ${list.length}. The maximum is ${LIMITS.maxHashtags}.`;
    const invalid = list.filter((tag) => this.normalizeHashtag(tag) !== tag);
    if (invalid.length) return `Invalid hashtag${invalid.length > 1 ? 's' : ''}: ${invalid.slice(0, 3).join(', ')}. Use letters in any script, numbers and underscores only.`;
    const lower = list.map((tag) => tag.toLowerCase());
    const dupes = [...new Set(lower.filter((tag, i) => lower.indexOf(tag) !== i))];
    if (dupes.length) return `Duplicate hashtags: ${dupes.join(', ')}. Each hashtag must be unique.`;
    return null;
  }

  /**
   * Validates a datetime-local value in the scheduling timezone.
   * @param {string} local @param {object} [settings]
   * @returns {{valid: boolean, error?: string, date?: Date}}
   */
  validateSchedule(local, settings = this.getSettings()) {
    if (!local) return { valid: false, error: 'Choose a date and time to publish.' };
    const zone = settings.timezone;
    if (!TimeZoneUtil.isValid(zone)) return { valid: false, error: `Unknown timezone "${zone}". Choose one in Settings → Scheduling.` };
    const result = TimeZoneUtil.zonedToUtc(local, zone);
    if (!result || Number.isNaN(result.date.getTime())) return { valid: false, error: 'Enter a valid date and time.' };
    if (!result.exists) return { valid: false, error: `That time does not exist in ${zone} because of a daylight-saving change. Pick another time.` };
    const now = Date.now();
    if (result.date.getTime() <= now) return { valid: false, error: 'The publish time is in the past. Choose a future date and time.' };
    if (result.date.getTime() < now + settings.minLeadMinutes * 60000) {
      return { valid: false, error: `Schedule at least ${pluralize(settings.minLeadMinutes, 'minute')} ahead (Settings → Scheduling).` };
    }
    if (result.date.getTime() > now + 2 * 365 * 864e5) return { valid: false, error: 'Schedule within the next two years.' };
    return { valid: true, date: result.date };
  }

  /**
   * @param {string} value @returns {string|null} error for an optional public media URL
   */
  validateMediaUrl(value) {
    const text = String(value ?? '').trim();
    if (!text) return null;
    let url;
    try { url = new URL(text); } catch { return 'Enter a full URL, for example https://cdn.example.com/photo.jpg.'; }
    if (url.protocol !== 'https:') return 'The image URL must start with https:// so Buffer can fetch it.';
    if (isLocalHost(url.hostname)) return 'Buffer cannot reach local addresses. Use a publicly hosted image URL.';
    return null;
  }

  /**
   * Checks that an image's post is complete and schedulable.
   * @param {object} item image item
   * @param {object} [settings]
   * @returns {{valid: boolean, errors: Object<string,string>, message?: string, date?: Date}}
   */
  validatePost(item, settings = this.getSettings()) {
    const errors = {};
    const content = item.content;
    if (!content || !String(content.description || '').trim()) {
      errors.description = 'Add a caption (generate it with AI or write one).';
    } else {
      const captionError = this.validateCaption(content.description, 'Caption', settings);
      if (captionError) errors.description = captionError;
      if (item.useAlternative) {
        const altError = this.validateCaption(content.alternative_description, 'Alternative caption', settings);
        if (altError) errors.alternative_description = altError;
      }
      const tagError = this.validateHashtags(content.hashtags);
      if (tagError) errors.hashtags = tagError;
      if (String(content.category || '').length > LIMITS.maxCategoryChars) errors.category = `Keep the category under ${LIMITS.maxCategoryChars} characters.`;
    }
    const schedule = this.validateSchedule(item.scheduleLocal, settings);
    if (!schedule.valid) errors.schedule = schedule.error;
    const mediaError = this.validateMediaUrl(item.mediaUrl);
    if (mediaError) errors.mediaUrl = mediaError;
    const keys = Object.keys(errors);
    return { valid: keys.length === 0, errors, message: keys.length ? errors[keys[0]] : undefined, date: schedule.date };
  }

  /**
   * Cleans up the model's JSON into the content shape, tolerating common
   * variations (aliases, string hashtags, 0–1 scores, hashtags in captions).
   * @param {object} raw parsed JSON object from the model
   * @param {object} [settings]
   * @returns {{content: object, issues: string[]}}
   * @throws {ApiError} kind "parse" when no caption can be recovered
   */
  normalizeGeneratedContent(raw, settings = this.getSettings()) {
    const data = {};
    for (const [key, value] of Object.entries(raw || {})) data[String(key).toLowerCase().replace(/[\s-]+/g, '_')] = value;
    const pick = (...keys) => { for (const key of keys) if (data[key] !== undefined && data[key] !== null) return data[key]; return undefined; };
    const issues = [];
    const inlineTags = [];
    const cleanCaption = (value) => {
      if (typeof value !== 'string') return '';
      let text = value.replace(/\r\n/g, '\n').split('\n').map((line) => line.replace(/[ \t]+/g, ' ').trim()).join('\n').trim();
      const stripQuotes = (s) => s.replace(/^["'“”‘’]+|["'“”‘’]+$/g, '').trim();
      text = stripQuotes(text);
      const trailing = /(?:\s*#[\p{L}\p{M}\p{N}_\u200c\u200d]+)+\s*$/u.exec(text);
      if (trailing && trailing.index > 0) {
        inlineTags.push(...trailing[0].trim().split(/\s+/));
        text = stripQuotes(text.slice(0, trailing.index).trim());
      }
      return [...text].slice(0, settings.descMax).join('');
    };

    let description = cleanCaption(pick('description', 'caption', 'social_media_caption', 'primary_caption', 'main_caption'));
    let alternative = cleanCaption(pick('alternative_description', 'alternative_caption', 'alt_description', 'alternate_caption', 'alternative', 'alt_caption'));
    if (!description && alternative) { description = alternative; alternative = ''; }
    if (!description) {
      throw new ApiError('The AI response did not include a caption.', { kind: 'parse', service: 'Ollama', details: truncate(JSON.stringify(raw), 400) });
    }
    if (!alternative) issues.push('No alternative caption was returned.');

    const rawTags = pick('hashtags', 'tags', 'hash_tags');
    const tagList = Array.isArray(rawTags) ? rawTags.map(String) : String(rawTags ?? '').split(/[\s,;]+/);
    let hashtags = this.normalizeHashtagList([...tagList, ...inlineTags]);
    if (hashtags.length > LIMITS.maxHashtags) {
      issues.push(`Trimmed hashtags from ${hashtags.length} to ${LIMITS.maxHashtags}.`);
      hashtags = hashtags.slice(0, LIMITS.maxHashtags);
    }

    let category = pick('category', 'content_category', 'categories');
    if (Array.isArray(category)) category = category[0];
    category = typeof category === 'string' ? category.trim().replace(/\s+/g, ' ').slice(0, LIMITS.maxCategoryChars) : '';
    if (category) category = category.charAt(0).toUpperCase() + category.slice(1);
    else { category = 'General'; issues.push('No category was returned; using "General".'); }

    if (hashtags.length < LIMITS.minHashtags) {
      const before = hashtags.length;
      hashtags = this.topUpHashtags(hashtags, `${category} ${description} ${alternative}`);
      if (hashtags.length > before) issues.push(`Added ${hashtags.length - before} hashtags to reach ${hashtags.length}.`);
    }

    let score = pick('engagement_score', 'engagement', 'score', 'estimated_engagement_score');
    if (typeof score === 'string') score = parseFloat(score.replace(/[^\d.]/g, ''));
    if (typeof score !== 'number' || !Number.isFinite(score)) {
      score = 50;
      issues.push('No engagement score was returned; using 50.');
    } else if (score > 0 && score <= 1) {
      score *= 100;
    }
    score = clamp(Math.round(score), 1, 100);

    return {
      content: { description, alternative_description: alternative, hashtags, category, engagement_score: score },
      issues
    };
  }

  /**
   * Adds hashtags derived from the text until the minimum count is reached.
   * @param {string[]} tags @param {string} text @returns {string[]}
   */
  topUpHashtags(tags, text) {
    const stop = new Set(RULES.hashtags.stopWords);
    const result = [...tags];
    const seen = new Set(result.map((t) => t.toLowerCase()));
    const words = String(text).toLowerCase().match(/[\p{L}\p{M}\p{N}]{4,}/gu) || [];
    for (const candidate of [...words.filter((w) => !stop.has(w)).map((w) => `#${w}`), ...RULES.hashtags.fallback]) {
      if (result.length >= LIMITS.minHashtags) break;
      const tag = this.normalizeHashtag(candidate);
      if (tag && !seen.has(tag.toLowerCase())) {
        seen.add(tag.toLowerCase());
        result.push(tag);
      }
    }
    return result;
  }

  /* -------------------------------------------------------------- settings */

  /**
   * Validates preferences. In Live mode (or with `requireCredentials`) the
   * server must also have the relevant service configured, because the
   * credentials and endpoints live there.
   * @param {object} settings
   * @param {{scope?: 'all'|'ollama'|'buffer', requireCredentials?: boolean}} [options]
   * @returns {{valid: boolean, errors: Object<string,string>, warnings: string[]}}
   */
  validateSettings(settings, { scope = 'all', requireCredentials } = {}) {
    const s = SettingsModel.normalize(settings);
    const live = requireCredentials ?? s.mode === 'live';
    const errors = {};
    const warnings = [];
    const inRange = (key, min, max, label) => {
      if (!Number.isInteger(s[key]) || s[key] < min || s[key] > max) errors[key] = `${label} must be a whole number from ${min} to ${max}.`;
    };

    if (scope === 'all' || scope === 'ollama') {
      if (live && !SERVER.ollama.configured) {
        errors.serverOllama = `Ollama is not set up on the server: ${SERVER.ollama.problem} Add it to the .env file next to app.py and restart Poster.`;
      }
      if (!s.model) errors.model = 'Enter a model name, for example gemma4:31b.';
      else if (!/^[\w.\-/:]{1,100}$/.test(s.model)) errors.model = 'Model names contain only letters, numbers and . - _ / : characters.';
      inRange('aiConcurrency', 1, 4, 'Parallel requests');
      if (charCount(s.extraInstructions) > LIMITS.maxExtraInstructions) errors.extraInstructions = `Extra instructions are limited to ${LIMITS.maxExtraInstructions} characters.`;
    }

    if ((scope === 'all' || scope === 'buffer') && live && !SERVER.buffer.configured) {
      const message = `Buffer is not set up on the server: ${SERVER.buffer.problem} Add it to the .env file next to app.py and restart Poster.`;
      if (scope === 'buffer') errors.serverBuffer = message;
      else warnings.push(`${message} Until then AI generation works, but scheduling does not.`);
    }

    if (scope === 'all') {
      if (!TimeZoneUtil.isValid(s.timezone)) errors.timezone = 'Choose a valid timezone.';
      inRange('intervalMinutes', 1, LIMITS.maxPlanMinutes, 'Planner interval');
      inRange('minLeadMinutes', 0, 1440, 'Minimum lead time');
      inRange('descMin', RULES.caption.minLow, RULES.caption.minHigh, 'Caption minimum length');
      inRange('descMax', RULES.caption.maxLow, RULES.caption.maxHigh, 'Caption maximum length');
      if (!errors.descMin && !errors.descMax && s.descMin >= s.descMax) errors.descMax = 'The maximum length must be greater than the minimum length.';
    }
    return { valid: Object.keys(errors).length === 0, errors, warnings };
  }

  /**
   * Validates an imported JSON document before anything is applied.
   * Accepts a Poster export, a bare settings object or a CONFIG-shaped object.
   * Credential and endpoint keys are ignored: they belong on the server.
   * @param {*} json
   * @param {object} currentSettings values kept for keys the import lacks
   * @returns {{valid: boolean, errors: string[], settings?: object, ignored: string[]}}
   */
  validateImportPayload(json, currentSettings) {
    if (!isPlainObject(json)) return { valid: false, errors: ['The file does not contain a JSON object.'], ignored: [] };
    let source;
    if (isPlainObject(json.settings)) {
      if (json.app && json.app !== 'Poster') return { valid: false, errors: [`This file was exported by "${String(json.app)}", not Poster.`], ignored: [] };
      source = json.settings;
    } else if (['MODEL', 'TIMEZONE', 'MODE', 'OLLAMA_ENDPOINT', 'OLLAMA_API_KEY', 'BUFFER_ACCESS_TOKEN'].some((key) => key in json)) {
      source = { ...SettingsModel.fromConfigShape(json) };
      for (const key of Object.keys(json)) if (SettingsModel.serverOnlyKeys.includes(key)) source[key] = json[key];
    } else {
      source = json;
    }
    const known = Object.keys(SettingsModel.defaults());
    const ignored = Object.keys(source).filter((key) => !known.includes(key) && key !== 'version');
    const recognised = Object.keys(source).filter((key) => known.includes(key) && key !== 'version');
    if (!recognised.length) {
      const serverOnly = ignored.filter((key) => SettingsModel.serverOnlyKeys.includes(key));
      return {
        valid: false,
        errors: [serverOnly.length
          ? 'This file only contains credentials or endpoints, which are now configured on the server (.env), not in the browser.'
          : 'No Poster settings were found in this file.'],
        ignored
      };
    }

    const typeErrors = [];
    const defaults = SettingsModel.defaults();
    for (const key of recognised) {
      const expected = Array.isArray(defaults[key]) ? 'array' : typeof defaults[key];
      const actual = Array.isArray(source[key]) ? 'array' : typeof source[key];
      const numericString = expected === 'number' && actual === 'string' && source[key].trim() !== '' && Number.isFinite(Number(source[key]));
      if (expected !== actual && !numericString) typeErrors.push(`"${key}" should be a ${expected}, not a ${actual}.`);
    }
    if (typeErrors.length) return { valid: false, errors: typeErrors, ignored };

    const imported = Object.fromEntries(recognised.map((key) => [key, source[key]]));
    const merged = SettingsModel.normalize({ ...currentSettings, ...imported });
    const result = this.validateSettings(merged, { requireCredentials: false });
    if (!result.valid) return { valid: false, errors: Object.values(result.errors), ignored };
    return { valid: true, errors: [], settings: merged, ignored };
  }
}
