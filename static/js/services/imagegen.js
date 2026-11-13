/** ImageGenService: text-to-image and image editing through the Poster server. */
import { API_CONFIG, LIMITS, SERVER } from '../config.js';
import { ApiError } from '../errors.js';
import { encodeScaledJpeg } from '../image.js';

/**
 * Wraps generated image data in a File named after the description, so it
 * joins the gallery like an upload (and Demo captions can use its words).
 * @param {BlobPart} data @param {string} prompt @param {string} mimeType
 * @param {string} [prefix] start of the file name: "ai" for generated images, "ai-edit" for edited ones
 * @param {number} [number] the image's place in a batch made from one description (0: not in a batch)
 * @returns {File}
 */
export function generatedImageFile(data, prompt, mimeType, prefix = 'ai', number = 0) {
  const words = prompt.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').match(/[\p{L}\p{M}\p{N}]+/gu) || [];
  let slug = '';
  for (const word of words) {
    if (slug.length + word.length + 1 > 48) break;
    slug = slug ? `${slug}-${word}` : word;
  }
  const ext = { 'image/png': 'png', 'image/webp': 'webp' }[mimeType] || 'jpg';
  return new File([data], `${prefix}-${slug || 'image'}${number ? `-${number}` : ''}.${ext}`, { type: mimeType, lastModified: Date.now() });
}

/**
 * Sends a description to /api/image/generate, or a photo and a description of
 * a change to /api/image/edit. The server calls Pollinations and returns the
 * image as Base64.
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

  /** @returns {boolean} whether editImage() can run: the active image provider edits images */
  get canEdit() { return this.available && SERVER.imageGen.canEdit !== false; }

  /** @throws {ApiError} kind "config" when the server has no image AI provider set up */
  assertConfigured() {
    if (!this.available) {
      throw new ApiError(`Image generation is not set up on the server: ${SERVER.imageGen.problem}`, {
        kind: 'config', service: SERVER.imageGen.label, hint: 'Set up an image AI provider in Settings → Credentials & Integrations.'
      });
    }
  }

  /**
   * Generates one image from a description. Every call makes a different image (the server picks a new seed).
   * @param {string} prompt what the image should show
   * @param {string} aspect square | portrait | landscape | story
   * @param {{signal?: AbortSignal, onRetry?: Function, number?: number}} [ctx] number: the image's place in a
   *   batch, which ends its file name ("ai-…-2.png")
   * @returns {Promise<File>}
   */
  async generateImage(prompt, aspect, { signal, onRetry, number = 0 } = {}) {
    this.assertConfigured();
    const data = await this.api.callWithRetry(API_CONFIG.routes.imageGenerate, {
      json: { prompt, aspect }, timeoutMs: API_CONFIG.timeouts.imageGenMs, signal, service: SERVER.imageGen.label, onRetry
    });
    return generatedImageFile(ImageGenService.imageBytes(data), prompt, data.mimeType, 'ai', number);
  }

  /**
   * Makes an updated copy of a photo, changed as the description says. The
   * photo itself is not changed; a downscaled JPEG copy is sent. Every call makes a different copy (the server
   * picks a new seed).
   * @param {Blob} file the photo to change
   * @param {string} prompt the change, e.g. "make it a watercolour painting"
   * @param {{signal?: AbortSignal, onRetry?: Function, number?: number}} [ctx] number: the copy's place in a
   *   batch, which ends its file name ("ai-edit-…-2.png")
   * @returns {Promise<File>}
   */
  async editImage(file, prompt, { signal, onRetry, number = 0 } = {}) {
    this.assertConfigured();
    if (!this.canEdit) {
      throw new ApiError(`${SERVER.imageGen.label} cannot edit images.`, { kind: 'config', service: SERVER.imageGen.label, hint: 'Choose another image provider in Settings → Credentials & Integrations.' });
    }
    const image = await encodeScaledJpeg(file, LIMITS.editMaxEdge, 0.9);
    const data = await this.api.callWithRetry(API_CONFIG.routes.imageEdit, {
      json: { prompt, image }, timeoutMs: API_CONFIG.timeouts.imageGenMs, signal, service: SERVER.imageGen.label, onRetry
    });
    return generatedImageFile(ImageGenService.imageBytes(data), prompt, data.mimeType, 'ai-edit', number);
  }

  /**
   * @param {{image?: unknown, mimeType?: unknown}} data the server's answer
   * @returns {Uint8Array} the image it carries as Base64
   * @throws {ApiError} kind "parse" when it carries none
   */
  static imageBytes(data) {
    if (typeof data.image !== 'string' || !LIMITS.allowedTypes.includes(data.mimeType)) {
      throw new ApiError('The Poster server returned no image.', { kind: 'parse', service: SERVER.imageGen.label });
    }
    const binary = atob(data.image);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
}
