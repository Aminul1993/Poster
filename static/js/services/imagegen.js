/** ImageGenService: text-to-image through the Poster server. */
import { API_CONFIG, LIMITS, SERVER } from '../config.js';
import { ApiError } from '../errors.js';

/**
 * Wraps generated image data in a File named after the description, so it
 * joins the gallery like an upload (and Demo captions can use its words).
 * @param {BlobPart} data @param {string} prompt @param {string} mimeType
 * @returns {File}
 */
export function generatedImageFile(data, prompt, mimeType) {
  const words = prompt.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').match(/[\p{L}\p{M}\p{N}]+/gu) || [];
  let slug = '';
  for (const word of words) {
    if (slug.length + word.length + 1 > 48) break;
    slug = slug ? `${slug}-${word}` : word;
  }
  const ext = { 'image/png': 'png', 'image/webp': 'webp' }[mimeType] || 'jpg';
  return new File([data], `ai-${slug || 'image'}.${ext}`, { type: mimeType, lastModified: Date.now() });
}

/**
 * Sends a description to /api/image/generate. The server calls Pollinations
 * with retries and returns the image as Base64.
 */
export class ImageGenService {
  /** @param {{api: ApiService}} deps */
  constructor({ api }) {
    this.api = api;
  }

  /** @returns {boolean} */
  get isDemo() { return false; }

  /** @returns {boolean} whether generateImage() can run */
  get available() { return SERVER.imageGen.configured; }

  /** @throws {ApiError} kind "config" when the server has no Pollinations key */
  assertConfigured() {
    if (!this.available) {
      throw new ApiError(`Image generation is not set up on the server: ${SERVER.imageGen.problem}`, {
        kind: 'config', service: 'Pollinations', hint: 'Add POLLINATIONS_API_KEY to the .env file next to app.py and restart Poster.'
      });
    }
  }

  /**
   * Generates one image from a description.
   * @param {string} prompt what the image should show
   * @param {string} aspect square | portrait | landscape | story
   * @param {{signal?: AbortSignal, onRetry?: Function}} [ctx]
   * @returns {Promise<File>}
   */
  async generateImage(prompt, aspect, { signal, onRetry } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.imageGenerate, {
      json: { prompt, aspect }, timeoutMs: API_CONFIG.timeouts.imageGenMs, signal, service: 'Pollinations', onRetry
    });
    if (typeof data.image !== 'string' || !LIMITS.allowedTypes.includes(data.mimeType)) {
      throw new ApiError('The Poster server returned no image.', { kind: 'parse', service: 'Pollinations' });
    }
    const binary = atob(data.image);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return generatedImageFile(bytes, prompt, data.mimeType);
  }
}
