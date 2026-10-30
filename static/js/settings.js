/** The settings model: defaults, normalisation, field metadata. */
import { LANGUAGES, RULES, SERVER, TONES } from './config.js';
import { TimeZoneUtil } from './time.js';
import { isPlainObject } from './util.js';

export const SettingsModel = {
  /**
   * Keys from older exports and CONFIG blocks that are now set only on the
   * server. Imports skip them and report them as ignored.
   */
  serverOnlyKeys: Object.freeze(['ollamaEndpoint', 'ollamaApiKey', 'ollamaTimeoutSec', 'bufferToken', 'bufferApiUrl',
    'bufferTimeoutSec', 'mediaEndpoint', 'mediaPreset', 'OLLAMA_ENDPOINT', 'OLLAMA_API_KEY', 'BUFFER_ACCESS_TOKEN',
    'BUFFER_API_URL', 'MEDIA_UPLOAD_ENDPOINT', 'MEDIA_UPLOAD_PRESET']),

  /** Demo channels, always available in Demo mode. */
  demoChannels: Object.freeze([
    { id: 'demo-instagram', name: 'Poster Studio', handle: 'poster.studio', service: 'instagram', organizationName: 'Demo workspace', usable: true },
    { id: 'demo-linkedin', name: 'Poster Inc.', handle: 'poster-inc', service: 'linkedin', organizationName: 'Demo workspace', usable: true },
    { id: 'demo-facebook', name: 'Poster Community', handle: 'postercommunity', service: 'facebook', organizationName: 'Demo workspace', usable: true },
    { id: 'demo-twitter', name: 'Poster', handle: 'posterapp', service: 'twitter', organizationName: 'Demo workspace', usable: true }
  ]),

  /** @returns {object} default preferences, derived from the server configuration */
  defaults() {
    return {
      version: 2,
      mode: SERVER.mode === 'live' ? 'live' : 'demo',
      model: String(SERVER.ollama.model || 'gemma4:31b').trim(),
      aiConcurrency: 2,
      tone: 'friendly',
      language: 'en',
      extraInstructions: '',
      channelIds: { live: [], demo: ['demo-instagram', 'demo-linkedin'] },
      requireMedia: true,
      timezone: TimeZoneUtil.browserZone(),
      intervalMinutes: 60,
      minLeadMinutes: 5,
      descMin: RULES.caption.defaultMin,
      descMax: RULES.caption.defaultMax
    };
  },

  /**
   * Coerces an arbitrary object into a complete, well-typed settings object.
   * Unknown keys are dropped; invalid types fall back to defaults.
   * @param {object} input
   * @returns {object}
   */
  normalize(input) {
    const d = this.defaults();
    const src = isPlainObject(input) ? input : {};
    const str = (key) => (typeof src[key] === 'string' ? src[key].trim() : d[key]);
    const int = (key) => {
      const n = typeof src[key] === 'string' && src[key].trim() !== '' ? Number(src[key]) : src[key];
      return Number.isFinite(n) ? Math.round(n) : d[key];
    };
    const bool = (key) => (typeof src[key] === 'boolean' ? src[key] : d[key]);
    const ids = (list, fallback) => (Array.isArray(list) ? [...new Set(list.filter((v) => typeof v === 'string' && v.trim()).map((v) => v.trim()))] : fallback);
    const channelSrc = isPlainObject(src.channelIds) ? src.channelIds : {};
    return {
      version: 2,
      mode: src.mode === 'live' || src.mode === 'demo' ? src.mode : d.mode,
      model: str('model') || d.model,
      aiConcurrency: int('aiConcurrency'),
      tone: Object.prototype.hasOwnProperty.call(TONES, src.tone) ? src.tone : d.tone,
      language: Object.prototype.hasOwnProperty.call(LANGUAGES, src.language) ? src.language : d.language,
      extraInstructions: typeof src.extraInstructions === 'string' ? src.extraInstructions.trim() : d.extraInstructions,
      channelIds: { live: ids(channelSrc.live, d.channelIds.live), demo: ids(channelSrc.demo, d.channelIds.demo) },
      requireMedia: bool('requireMedia'),
      timezone: TimeZoneUtil.modern(str('timezone')),
      intervalMinutes: int('intervalMinutes'),
      minLeadMinutes: int('minLeadMinutes'),
      descMin: int('descMin'),
      descMax: int('descMax')
    };
  },

  /**
   * Maps a CONFIG-shaped object (MODEL, TIMEZONE, MODE …) to settings keys.
   * Credential and endpoint keys are ignored: they belong on the server.
   * @param {object} source @returns {object}
   */
  fromConfigShape(source) {
    const map = { MODEL: 'model', TIMEZONE: 'timezone', MODE: 'mode' };
    const out = {};
    for (const [from, to] of Object.entries(map)) {
      if (from in source && typeof source[from] === 'string' && source[from].trim()) out[to] = source[from].trim();
    }
    if (out.mode === 'auto') delete out.mode;
    return out;
  }
};
