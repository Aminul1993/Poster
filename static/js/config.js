/** Configuration from the server, API routes, limits, timings and storage keys. */

/**
 * Content rules, used only when the server's configuration is missing. The
 * server sends the real ones in server-config ("rules"), so the browser and the
 * server apply the same rules.
 */
const FALLBACK_RULES = {
  tones: {
    friendly: 'warm, conversational and approachable',
    professional: 'polished, credible and concise',
    playful: 'fun, witty and light-hearted; emojis welcome',
    inspirational: 'uplifting and motivating',
    luxury: 'elegant, refined and exclusive',
    bold: 'energetic, confident and attention-grabbing'
  },
  hashtags: { min: 10, max: 15, fallback: ['#instagood', '#photooftheday', '#contentcreator', '#dailyinspiration', '#socialmedia'], stopWords: [] },
  maxTextChars: 10000,
  maxExtraChars: 500,
  maxCategoryChars: 40,
  caption: { minLow: 1, minHigh: 500, maxLow: 20, maxHigh: 10000, defaultMin: 10, defaultMax: 2200 },
  prompt: { min: 3, max: 1000 },
  postFilters: { all: null, upcoming: ['scheduled', 'needs_approval', 'sending'], published: ['sent'] },
  publishedStatuses: ['sending', 'sent'],
  retry: { maxRetries: 3, delaysMs: [1000, 2000, 4000], retryableStatus: [429, 500, 502, 503, 504], retryableKinds: ['network', 'rate_limit', 'timeout'] }
};

/** @template T @param {T} value @returns {T} the value, frozen all the way down */
function deepFreeze(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

/**
 * Non-secret configuration rendered by Flask into #server-config: which
 * services are configured on the server, the default model and mode,
 * timeouts and the content rules. Falls back to "nothing configured" when it
 * is missing.
 */
export const SERVER = (() => {
  const missing = 'Server configuration unavailable.';
  const fallback = {
    version: '',
    mode: 'demo',
    ollama: { configured: false, problem: missing, model: 'gemma4:31b', models: [], host: '', timeout: 120, requestTimeout: 1000 },
    buffer: { configured: false, problem: missing, tokenId: '', host: '', timeout: 30, requestTimeout: 160 },
    media: { configured: false, problem: missing, host: '', requestTimeout: 500 },
    imageGen: { configured: false, problem: missing, host: '', aspects: {}, editModel: '', maxPromptChars: 1000, requestTimeout: 260 },
    limits: {},
    rules: FALLBACK_RULES
  };
  try {
    const parsed = JSON.parse(document.getElementById('server-config')?.textContent || 'null');
    if (!parsed || typeof parsed !== 'object') return fallback;
    return {
      ...fallback,
      ...parsed,
      ollama: { ...fallback.ollama, ...parsed.ollama },
      buffer: { ...fallback.buffer, ...parsed.buffer },
      media: { ...fallback.media, ...parsed.media },
      imageGen: { ...fallback.imageGen, ...parsed.imageGen },
      rules: { ...FALLBACK_RULES, ...parsed.rules }
    };
  } catch {
    return fallback;
  }
})();

/** The content rules the server applies too (see FALLBACK_RULES for their shape). */
export const RULES = deepFreeze(SERVER.rules);

/**
 * Central API configuration: every server route, timeout and retry rule.
 * The server performs the retries towards Ollama and Buffer; the browser only
 * retries when it cannot reach the Poster server itself.
 */
export const API_CONFIG = Object.freeze({
  routes: Object.freeze({
    ollamaTest: '/api/ollama/test',
    ollamaGenerate: '/api/ollama/generate',
    bufferValidate: '/api/buffer/validate',
    bufferConnect: '/api/buffer/connect',
    bufferSchedule: '/api/buffer/schedule',
    bufferPosts: '/api/buffer/posts',
    bufferEditPosts: '/api/buffer/posts/edit',
    bufferDeletePosts: '/api/buffer/posts/delete',
    mediaUpload: '/api/media/upload',
    imageGenerate: '/api/image/generate',
    imageEdit: '/api/image/edit'
  }),
  /** Multipart field the server reads the image from. */
  mediaFileField: 'file',
  timeouts: Object.freeze({
    testMs: 90000,
    generateMs: SERVER.ollama.requestTimeout * 1000,
    bufferMs: SERVER.buffer.requestTimeout * 1000,
    mediaMs: SERVER.media.requestTimeout * 1000,
    imageGenMs: SERVER.imageGen.requestTimeout * 1000
  }),
  retry: RULES.retry
});

/** Hard limits and tunables. */
export const LIMITS = Object.freeze({
  maxImages: 10,
  maxImageBytes: 20 * 1024 * 1024,
  /** Largest upload the server accepts (4 MB on Vercel); bigger images are re-encoded to fit. */
  maxUploadBytes: Number(SERVER.limits.maxUploadBytes) || 20 * 1024 * 1024,
  /** Longest edge tried when re-encoding an upload that is too large; smaller edges follow if needed. */
  uploadMaxEdge: 4096,
  uploadMinEdge: 1280,
  allowedTypes: Object.freeze(['image/jpeg', 'image/png', 'image/webp']),
  allowedExtensions: Object.freeze(['jpg', 'jpeg', 'png', 'webp']),
  minHashtags: RULES.hashtags.min,
  maxHashtags: RULES.hashtags.max,
  /** Longest post text the server sends to Buffer. */
  maxPostChars: RULES.maxTextChars,
  maxCategoryChars: RULES.maxCategoryChars,
  /** How far the post preview may run past the caption maximum (hashtags included) before it is flagged. */
  previewSlackChars: 500,
  /** Longest planner interval: 7 days, in minutes. */
  maxPlanMinutes: 10080,
  analysisMaxEdge: 1280,
  /** Longest edge of the copy of a photo sent to be edited with AI; the models work at about 1 megapixel. */
  editMaxEdge: 1536,
  /** Gallery and editor thumbnails; matches the thumbnails the server stores. */
  previewMaxEdge: 400,
  thumbMaxEdge: 160,
  maxLogEntries: 300,
  /** Entries kept when browser storage is full. */
  logEntriesWhenFull: 80,
  maxDrafts: 150,
  draftsWhenFull: 40,
  /** Pages of upcoming posts read when looking for a post whose creation answer was lost. */
  reconcilePages: 5,
  maxExtraInstructions: RULES.maxExtraChars,
  minPromptChars: RULES.prompt.min,
  maxPromptChars: Number(SERVER.imageGen.maxPromptChars) || RULES.prompt.max
});

/** Delays and durations, in milliseconds. */
export const TIMING = Object.freeze({
  toastMs: Object.freeze({ error: 9000, warning: 7000, info: 4500, success: 4500 }),
  logPersistMs: 500,
  draftPersistMs: 600,
  opTickerMs: 1000,
  /** Minimum gap between two "storage problem" toasts. */
  storageToastGapMs: 20000
});

/** localStorage keys (namespaced by StorageService). */
export const STORAGE_KEYS = Object.freeze({
  settings: 'settings',
  theme: 'theme',
  ui: 'ui',
  drafts: 'drafts',
  /** No longer written: scheduled posts come from Buffer. Old copies are removed at start. */
  schedules: 'schedules',
  log: 'log',
  connection: 'connection'
});

export const APP_VERSION = SERVER.version || '2.1.0';

/** Tone presets offered in Settings, with the wording sent to the model. */
export const TONES = RULES.tones;
