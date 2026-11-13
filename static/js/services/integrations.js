/** IntegrationsService: Settings → Credentials & Integrations, through the Poster server's /api/settings routes. */
import { API_CONFIG, refreshServer } from '../config.js';

/** URL slug of each integration type. */
export const INTEGRATION_SLUGS = Object.freeze({ text_ai: 'text-ai', image_ai: 'image-ai', storage: 'storage', meta: 'meta' });

/**
 * The settings a form holds, as the server takes them: a secret left blank is left out (the server keeps the
 * saved one), list fields become arrays.
 * @param {Array<{key: string, kind: string}>} fields the provider's fields
 * @param {Object<string, string|string[]>} values what the form holds, by field key
 * @returns {Object<string, string|string[]>}
 */
export function settingsPayload(fields, values) {
  const out = {};
  for (const field of fields) {
    const value = values[field.key];
    if (field.kind === 'secret') {
      if (typeof value === 'string' && value.trim()) out[field.key] = value.trim();
    } else if (field.kind === 'pages' || field.kind === 'list') {
      out[field.key] = Array.isArray(value) ? value : String(value || '').split(',').map((v) => v.trim()).filter(Boolean);
    } else {
      out[field.key] = typeof value === 'string' ? value.trim() : '';
    }
  }
  return out;
}

/**
 * Problems the browser can see before asking the server, by field key: a required field left empty (a secret
 * counts as set when the server already has one), and an address that is not https://.
 * @param {Array<object>} fields the provider's fields, as the server describes them
 * @param {Object<string, string|string[]>} values
 * @returns {Object<string, string>}
 */
export function validateSettings(fields, values) {
  const errors = {};
  for (const field of fields) {
    const raw = values[field.key];
    const value = Array.isArray(raw) ? raw.join(',') : String(raw ?? '').trim();
    if (field.required && !value && !(field.kind === 'secret' && field.hasValue) && !field.default) {
      errors[field.key] = `${field.label} is required.`;
    } else if (value && field.kind === 'url' && !/^https:\/\/[^\s/]+/i.test(value) && !/^http:\/\/(localhost|127\.)/i.test(value)) {
      errors[field.key] = `${field.label} must be a full https:// address.`;
    } else if (value && field.kind === 'secret' && /\s/.test(value)) {
      errors[field.key] = `${field.label} contains whitespace.`;
    }
  }
  return errors;
}

export class IntegrationsService {
  /** @param {{api: import('../api.js').ApiService}} deps */
  constructor({ api }) {
    this.api = api;
    /** The administrator password (POSTER_ADMIN_PASSWORD), kept in memory only: never in storage. */
    this.adminPassword = '';
  }

  /** @returns {object} options every request shares */
  options(extra = {}) {
    return {
      timeoutMs: API_CONFIG.timeouts.integrationsMs,
      service: 'Settings',
      headers: this.adminPassword ? { 'X-Poster-Admin': this.adminPassword } : {},
      ...extra
    };
  }

  /** @returns {Promise<object>} every integration, its providers, their (masked) settings and status */
  load() {
    return this.api.get(API_CONFIG.routes.integrations, this.options());
  }

  /** @returns {Promise<{services: Array<object>}>} each integration's health */
  status() {
    return this.api.get(API_CONFIG.routes.integrationsStatus, this.options());
  }

  /**
   * Saves a provider's settings and makes it the active one.
   * @param {string} type text_ai | image_ai | storage | meta @param {string} provider
   * @param {object} config see settingsPayload() @param {{activate?: boolean}} [options]
   * @returns {Promise<{integration: object, status: Array<object>}>}
   */
  save(type, provider, config, { activate = true } = {}) {
    return this.api.post(`${API_CONFIG.routes.integrations}/${INTEGRATION_SLUGS[type]}`, this.options({ json: { provider, config, activate } }));
  }

  /**
   * Tests a provider: with `config`, the unsaved settings in the form; without, the saved ones.
   * @param {string} type @param {string} provider @param {object|null} [config]
   * @returns {Promise<object>} ok, detail, and the provider's refreshed description (integration)
   */
  test(type, provider, config = null) {
    return this.api.post(`${API_CONFIG.routes.integrationsTest}/${INTEGRATION_SLUGS[type]}`, this.options({ json: { provider, config } }));
  }

  /**
   * The models a text AI provider's account can use, with the settings in the form.
   * @param {string} provider @param {object} config @returns {Promise<{models: string[]}>}
   */
  models(provider, config) {
    return this.api.post(API_CONFIG.routes.textAiModels, this.options({ json: { provider, config } }));
  }

  /** Reloads the server's configuration into SERVER, so the dashboard follows a switch at once. */
  async refreshServer() {
    refreshServer(await this.api.get(API_CONFIG.routes.serverConfig, { timeoutMs: 30000 }));
  }
}
