/** Small helpers: DOM building, formatting, ids, debounce, downloads. */

/** @param {string} selector @param {ParentNode} [root] @returns {any} */
export const $ = (selector, root = document) => root.querySelector(selector);

/**
 * Creates a DOM element. Text is always set via textContent, so user data is
 * never parsed as HTML.
 * @param {string} tag
 * @param {Object<string, any>} [attrs] class, text, dataset, on<event>, boolean props or attributes
 * @param {Array<Node|string|null|false>|Node|string} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (typeof value === 'boolean' && key in node) node[key] = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/**
 * Builds an inline SVG icon that references the sprite in the page.
 * @param {string} name icon id without the "i-" prefix
 * @param {string} [className]
 * @returns {SVGSVGElement}
 */
export function icon(name, className = '') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', `icon ${className}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

/** @param {number} bytes @returns {string} human readable size */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

/** @param {number} ms @returns {string} e.g. "1m 05s" */
export function formatDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  return `${minutes}m ${String(total % 60).padStart(2, '0')}s`;
}

let relativeFormat = null;

/**
 * Relative time such as "in 3 hours" or "5 minutes ago".
 * @param {Date} date
 * @returns {string}
 */
export function relativeTime(date) {
  const diff = date.getTime() - Date.now();
  const abs = Math.abs(diff);
  const units = [['year', 31536e6], ['month', 2592e6], ['week', 6048e5], ['day', 864e5], ['hour', 36e5], ['minute', 6e4]];
  try {
    relativeFormat ??= new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
    const rtf = relativeFormat;
    for (const [unit, size] of units) {
      if (abs >= size) return rtf.format(Math.round(diff / size), unit);
    }
    return rtf.format(Math.round(diff / 1000), 'second');
  } catch {
    return date.toLocaleString();
  }
}

/** @param {string} [prefix] @returns {string} a reasonably unique id */
export function uid(prefix = 'id') {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

/** @param {number} value @param {number} min @param {number} max @returns {number} */
export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/**
 * @template {Function} F
 * @param {F} fn
 * @param {number} wait
 * @returns {F & {flush: Function}}
 */
export function debounce(fn, wait) {
  let timer = null;
  let lastArgs = null;
  const debounced = (...args) => {
    lastArgs = args;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...lastArgs); }, wait);
  };
  debounced.flush = () => {
    if (timer) { clearTimeout(timer); timer = null; fn(...lastArgs); }
  };
  return debounced;
}

/** @param {*} value @returns {boolean} */
export function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

let graphemes = null;

/**
 * Shortens text to at most `max` user-perceived characters, cutting only between whole graphemes so a
 * Bengali conjunct or an emoji is never split in half.
 * @param {string} text @param {number} max @returns {string}
 */
export function truncate(text, max) {
  const value = String(text ?? '');
  if (value.length <= max) return value;
  let parts;
  try {
    graphemes ??= new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    parts = Array.from(graphemes.segment(value), (s) => s.segment);
  } catch {
    parts = [...value];
  }
  return parts.length > max ? `${parts.slice(0, Math.max(0, max - 1)).join('').trimEnd()}…` : value;
}

/** Scripts Poster's posts are written in, and the language tag that makes screen readers pick the right voice. */
const SCRIPT_LANGS = [
  [/\p{Script=Bengali}/gu, 'bn'], [/\p{Script=Devanagari}/gu, 'hi'], [/\p{Script=Tamil}/gu, 'ta'],
  [/\p{Script=Telugu}/gu, 'te'], [/\p{Script=Gujarati}/gu, 'gu'], [/\p{Script=Gurmukhi}/gu, 'pa'],
  [/\p{Script=Kannada}/gu, 'kn'], [/\p{Script=Malayalam}/gu, 'ml'], [/\p{Script=Sinhala}/gu, 'si'], [/\p{Script=Oriya}/gu, 'or']
];

/**
 * The language of text written mostly in one South Asian script, or '' for Latin or mixed text.
 * @param {string} text @returns {string}
 */
export function scriptLang(text) {
  const value = String(text ?? '');
  const letters = (value.match(/\p{L}/gu) || []).length;
  if (!letters) return '';
  for (const [pattern, lang] of SCRIPT_LANGS) {
    if ((value.match(pattern) || []).length * 2 > letters) return lang;
  }
  return '';
}

/** Hashtags at the end of a post (letters, marks, digits, _ and the zero-width joiners, which are format characters). */
const TRAILING_TAGS = /(?:\s*#[\p{L}\p{M}\p{N}_\p{Cf}]+)+\s*$/u;

/** @param {string} text @returns {{caption: string, tags: string}} the post text split into its caption and trailing hashtags */
export function splitPostText(text) {
  const value = String(text ?? '');
  const match = TRAILING_TAGS.exec(value);
  if (!match || match.index === 0) return { caption: value.trim(), tags: '' };
  return { caption: value.slice(0, match.index).trim(), tags: match[0].trim().replace(/\s+/g, ' ') };
}

/**
 * Fills `node` with `text`, giving each run of words in a South Asian script its own lang, so a screen reader
 * switches voice between Bengali and English inside one caption.
 * @param {HTMLElement} node @param {string} text @returns {HTMLElement} node
 */
export function scriptRuns(node, text) {
  node.removeAttribute('lang');
  const runs = [];
  for (const part of String(text ?? '').split(/(\s+)/)) {
    // Whitespace and punctuation stay in the run they sit in.
    const lang = /\p{L}/u.test(part) ? scriptLang(part) : null;
    const last = runs[runs.length - 1];
    if (last && (lang === null || lang === last.lang)) last.text += part;
    else runs.push({ lang: lang ?? '', text: part });
  }
  node.replaceChildren(...runs.map((run) => (run.lang ? el('span', { lang: run.lang, text: run.text }) : document.createTextNode(run.text))));
  return node;
}

/** Sets or clears an element's lang attribute from the script of its text. @param {HTMLElement} node @param {string} text */
export function applyScriptLang(node, text) {
  const lang = scriptLang(text);
  if (lang) node.setAttribute('lang', lang);
  else node.removeAttribute('lang');
}

/** @param {number} count @param {string} singular @param {string} [plural] @returns {string} */
export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

/** Number of Unicode code points, the unit the length limits are checked in (a Bengali conjunct counts as several). */
export function charCount(text) {
  return [...String(text ?? '')].length;
}

/**
 * Reads a Blob as a data: URL.
 * @param {Blob} blob @returns {Promise<string>}
 */
export function readAsDataURL(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error('The file could not be read.'));
    reader.readAsDataURL(blob);
  });
}

/**
 * Triggers a browser download of text content.
 * @param {string} filename @param {string} content @param {string} [type]
 */
export function downloadFile(filename, content, type = 'application/json') {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: filename, style: 'display:none' });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Decides whether a URL's host is the local machine. */
export function isLocalHost(hostname) {
  return /^(localhost|127(?:\.\d{1,3}){3}|\[::1\]|0\.0\.0\.0)$/i.test(hostname) || hostname.endsWith('.localhost');
}
