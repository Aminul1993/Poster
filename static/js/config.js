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
  languages: { en: 'English', bn: 'Bengali', 'bn-en': 'Bengali + English', hi: 'Hindi', 'hi-en': 'Hindi + English', ta: 'Tamil', 'ta-en': 'Tamil + English' },
  hashtags: { min: 10, max: 15, fallback: ['#instagood', '#photooftheday', '#contentcreator', '#dailyinspiration', '#socialmedia'], stopWords: [] },
  maxTextChars: 10000,
  maxExtraChars: 500,
  maxCategoryChars: 40,
  caption: { minLow: 1, minHigh: 500, maxLow: 20, maxHigh: 10000, defaultMin: 10, defaultMax: 2200 },
  prompt: { min: 3, max: 1000 },
  postFilters: { all: null, upcoming: ['scheduled', 'sending'], published: ['sent'] },
  publishedStatuses: ['sending', 'sent'],
  activity: {
    filters: { all: null, error: { levels: ['error'] }, warn: { levels: ['warn'], categories: ['retry'] }, success: { levels: ['success'] }, user: { categories: ['user', 'settings', 'upload'] } },
    maxBatch: 100
  },
  instagram: { maxCaptionChars: 2200, maxHashtags: 30 },
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
    groq: { configured: false, problem: missing, model: 'qwen/qwen3.8-27b', models: [], host: '', timeout: 120, requestTimeout: 1000 },
    social: {
      configured: false, problem: missing, tokenId: '', host: '', version: '', timeout: 30, requestTimeout: 160,
      database: { configured: false, problem: missing, kind: '' }, cronSecretSet: false
    },
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
      groq: { ...fallback.groq, ...parsed.groq },
      social: { ...fallback.social, ...parsed.social },
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
 * The server performs the retries towards Groq and Meta; the browser only
 * retries when it cannot reach the Poster server itself.
 */
export const API_CONFIG = Object.freeze({
  routes: Object.freeze({
    groqTest: '/api/groq/test',
    groqGenerate: '/api/groq/generate',
    socialValidate: '/api/social/validate',
    socialConnect: '/api/social/connect',
    socialSchedule: '/api/social/schedule',
    socialPosts: '/api/social/posts',
    socialEditPosts: '/api/social/posts/edit',
    socialDeletePosts: '/api/social/posts/delete',
    activitySave: '/api/activity',
    activityList: '/api/activity/list',
    activityClear: '/api/activity/clear',
    mediaUpload: '/api/media/upload',
    imageGenerate: '/api/image/generate',
    imageEdit: '/api/image/edit'
  }),
  /** Multipart field the server reads the image from. */
  mediaFileField: 'file',
  timeouts: Object.freeze({
    testMs: 90000,
    generateMs: SERVER.groq.requestTimeout * 1000,
    socialMs: SERVER.social.requestTimeout * 1000,
    mediaMs: SERVER.media.requestTimeout * 1000,
    imageGenMs: SERVER.imageGen.requestTimeout * 1000,
    activityMs: 30000
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
  /** Longest post text the server accepts. */
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
  /** Activity log entries kept in browser storage: those not saved to the server yet, or all of them without a database. */
  maxLogEntries: 300,
  /** Entries kept when browser storage is full. */
  logEntriesWhenFull: 80,
  maxDrafts: 150,
  draftsWhenFull: 40,
  maxExtraInstructions: RULES.maxExtraChars,
  minPromptChars: RULES.prompt.min,
  maxPromptChars: Number(SERVER.imageGen.maxPromptChars) || RULES.prompt.max
});

/** Delays and durations, in milliseconds. */
export const TIMING = Object.freeze({
  toastMs: Object.freeze({ error: 9000, warning: 7000, info: 4500, success: 4500 }),
  logPersistMs: 500,
  /** Wait before saving activity log entries again after the server could not be reached. */
  logRetryMs: 30000,
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
  /** No longer written: scheduled posts come from the server's queue. Old copies are removed at start. */
  schedules: 'schedules',
  log: 'log',
  connection: 'connection'
});

export const APP_VERSION = SERVER.version || '2.1.0';

/** Tone presets offered in Settings, with the wording sent to the model. */
export const TONES = RULES.tones;
/** Caption languages the model is asked to write in, by key, with their names. */
export const LANGUAGES = RULES.languages;
