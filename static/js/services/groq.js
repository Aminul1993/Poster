/** GroqService: image analysis and copywriting through the Poster server. */
import { API_CONFIG, SERVER } from '../config.js';
import { ApiError, throwIfAborted } from '../errors.js';
import { prepareAnalysisImage } from '../image.js';
import { isPlainObject } from '../util.js';

/**
 * Sends the resized image and the user's preferences to /api/groq/generate.
 * The server builds the prompt, calls Groq with retries, and validates and
 * normalises the JSON reply (including one automatic "repair" turn).
 */
export class GroqService {
  /**
   * @param {{api: ApiService, validation: ValidationService, notifier: NotificationService, getSettings: () => object}} deps
   */
  constructor({ api, validation, notifier, getSettings }) {
    this.api = api;
    this.validation = validation;
    this.notifier = notifier;
    this.getSettings = getSettings;
  }

  /** @returns {boolean} */
  get isDemo() { return false; }

  /** @throws {ApiError} kind "config" when the server has no Groq setup */
  assertConfigured() {
    if (!SERVER.groq.configured) {
      throw new ApiError(`Groq is not set up on the server: ${SERVER.groq.problem}`, {
        kind: 'config', service: 'Groq', hint: 'Add it to the .env file next to app.py and restart Poster.'
      });
    }
  }

  /**
   * The preferences the server needs to build the prompt.
   * @param {object} settings @param {object} [item] the image, whose own caption language wins over the batch's
   * @returns {object}
   */
  options(settings, item) {
    return {
      model: settings.model,
      tone: settings.tone,
      language: item?.language || settings.language,
      extraInstructions: settings.extraInstructions,
      descMin: settings.descMin,
      descMax: settings.descMax
    };
  }

  /**
   * Asks the server to list the endpoint's models and check the chosen one.
   * @param {{settings?: object, signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<{models: string[], modelAvailable: boolean|null, host: string}>}
   */
  async testConnection({ settings, signal, onRetry } = {}) {
    const s = settings ?? this.getSettings();
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.groqTest, {
      json: this.options(s), timeoutMs: API_CONFIG.timeouts.testMs, signal, service: 'Groq', onRetry
    });
    return {
      models: Array.isArray(data.models) ? data.models.map(String) : [],
      modelAvailable: typeof data.modelAvailable === 'boolean' ? data.modelAvailable : null,
      host: String(data.host || SERVER.groq.host || '')
    };
  }

  /**
   * Analyses one image and returns normalised social content.
   * @param {object} item image item with a `file`
   * @param {{signal?: AbortSignal, onRetry?: Function, settings?: object}} [ctx]
   * @returns {Promise<{content: object, issues: string[], model: string, repaired: boolean}>}
   */
  async generateContent(item, { signal, onRetry, settings } = {}) {
    const s = settings ?? this.getSettings();
    this.assertConfigured();
    let image;
    try {
      image = await prepareAnalysisImage(item);
    } catch (error) {
      throw new ApiError(`"${item.name}" could not be prepared for analysis: ${error.message}`, { kind: 'validation', service: 'Groq' });
    }
    throwIfAborted(signal);
    const data = await this.api.callWithRetry(API_CONFIG.routes.groqGenerate, {
      json: { ...this.options(s, item), image, fileName: item.name },
      timeoutMs: API_CONFIG.timeouts.generateMs,
      signal,
      service: 'Groq',
      onRetry
    });
    if (!isPlainObject(data.content)) {
      throw new ApiError('The Poster server returned no content.', { kind: 'parse', service: 'Groq' });
    }
    // The server already validated the reply; normalising again guarantees the editor's exact shape.
    const result = this.validation.normalizeGeneratedContent(data.content, s);
    const issues = [...new Set([...(Array.isArray(data.issues) ? data.issues.map(String) : []), ...result.issues])];
    if (data.repaired) {
      this.notifier.log('warn', 'ai', `Malformed AI reply for "${item.name}"; the server asked the model to correct it.`);
    }
    return { content: result.content, issues, model: String(data.model || s.model), repaired: Boolean(data.repaired) };
  }
}
