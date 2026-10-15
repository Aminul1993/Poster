/** Image helpers: decode, thumbnails, colour statistics, Base64. */
import { LIMITS } from './config.js';
import { ApiError } from './errors.js';
import { formatBytes, readAsDataURL } from './util.js';

/**
 * Decodes an image file into something drawable on a canvas.
 * @param {Blob} file
 * @returns {Promise<ImageBitmap|HTMLImageElement>}
 */
export async function decodeImage(file) {
  if (typeof createImageBitmap === 'function') {
    try { return await createImageBitmap(file); } catch { /* fall back to <img> decoding below */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    await img.decode();
    return img;
  } catch {
    throw new Error('The image could not be decoded. The file may be corrupted.');
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Draws `source` scaled so its longest edge is at most `maxEdge`, on a white
 * background (so transparent PNGs do not turn black as JPEG).
 * @param {ImageBitmap|HTMLImageElement|HTMLCanvasElement} source @param {number} maxEdge
 * @param {{readable?: boolean}} [options] readable: the pixels will be read back (getImageData), which is
 *   faster on a canvas made for it but makes drawing and encoding slower
 * @returns {HTMLCanvasElement}
 */
export function drawScaled(source, maxEdge, { readable = false } = {}) {
  const width = source.naturalWidth || source.width;
  const height = source.naturalHeight || source.height;
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext('2d', { willReadFrequently: readable });
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/**
 * Shows an item's thumbnail in `img`, never the original file: the one the
 * server stored once the image is uploaded, else the one made in the browser,
 * which also stands in when the stored one cannot load.
 * @param {HTMLImageElement} img @param {object} item
 */
export function showThumbnail(img, item) {
  const url = item.uploadedThumbUrl || item.previewUrl;
  if (img.dataset.thumb === url) return;
  img.dataset.thumb = url;
  img.onerror = url === item.previewUrl ? null : () => { img.onerror = null; img.src = item.previewUrl; };
  img.src = url;
}

/**
 * Average colour, brightness, saturation and a coarse colour name.
 * @param {HTMLCanvasElement} canvas
 * @returns {{r: number, g: number, b: number, brightness: number, saturation: number, hue: number, colorName: string}}
 */
export function computeImageStats(canvas) {
  const { data } = canvas.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, canvas.width, canvas.height);
  let r = 0; let g = 0; let b = 0; let sat = 0; let n = 0;
  for (let i = 0; i < data.length; i += 16) {
    const pr = data[i]; const pg = data[i + 1]; const pb = data[i + 2];
    r += pr; g += pg; b += pb;
    const max = Math.max(pr, pg, pb); const min = Math.min(pr, pg, pb);
    sat += max === 0 ? 0 : (max - min) / max;
    n += 1;
  }
  r /= n; g /= n; b /= n;
  const brightness = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const saturation = sat / n;
  const max = Math.max(r, g, b); const min = Math.min(r, g, b);
  let hue = 0;
  if (max !== min) {
    const d = max - min;
    if (max === r) hue = ((g - b) / d) % 6;
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue = Math.round(hue * 60 + 360) % 360;
  }
  let colorName = 'neutral';
  if (brightness < 0.22) colorName = 'dark';
  else if (saturation < 0.16) colorName = brightness > 0.72 ? 'bright' : 'neutral';
  else if (hue < 15 || hue >= 345) colorName = 'red';
  else if (hue < 42) colorName = 'orange';
  else if (hue < 70) colorName = 'golden';
  else if (hue < 165) colorName = 'green';
  else if (hue < 200) colorName = 'teal';
  else if (hue < 255) colorName = 'blue';
  else if (hue < 290) colorName = 'purple';
  else colorName = 'pink';
  return { r: Math.round(r), g: Math.round(g), b: Math.round(b), brightness, saturation, hue, colorName };
}

/**
 * Encodes a canvas as an image file.
 * @param {HTMLCanvasElement} canvas @param {string} type @param {number} quality
 * @returns {Promise<Blob|null>} null when the browser could not encode it
 */
export function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

/**
 * Encodes a canvas as Base64 JPEG (without the data: prefix).
 * @param {HTMLCanvasElement} canvas @param {number} quality
 * @returns {Promise<string>}
 */
export async function canvasToBase64Jpeg(canvas, quality) {
  const blob = await canvasToBlob(canvas, 'image/jpeg', quality);
  const dataUrl = blob ? await readAsDataURL(blob) : canvas.toDataURL('image/jpeg', quality);
  const comma = dataUrl.indexOf(',');
  if (comma === -1) throw new Error('The image could not be encoded.');
  return dataUrl.slice(comma + 1);
}

/**
 * Resized Base64 JPEG for the vision model (cached on the item, memory only).
 * Sending a 1280 px preview keeps requests small without hurting analysis.
 * @param {object} item @returns {Promise<string>}
 */
export async function prepareAnalysisImage(item) {
  if (item.analysisBase64) return item.analysisBase64;
  const source = await decodeImage(item.file);
  try {
    const canvas = drawScaled(source, LIMITS.analysisMaxEdge);
    item.analysisBase64 = await canvasToBase64Jpeg(canvas, 0.85);
    return item.analysisBase64;
  } finally {
    if (typeof source.close === 'function') source.close();
  }
}

/**
 * The image to send to the media host. Files over the server's upload limit
 * are re-encoded as JPEG (cached on the item, memory only), shrinking until one fits.
 * @param {object} item @returns {Promise<{blob: Blob, name: string}>}
 */
export async function prepareUploadImage(item) {
  if (item.file.size <= LIMITS.maxUploadBytes) return { blob: item.file, name: item.name };
  if (item.uploadImage) return item.uploadImage;
  const source = await decodeImage(item.file);
  try {
    const longest = Math.max(source.naturalWidth || source.width, source.naturalHeight || source.height);
    const name = `${item.name.replace(/\.[^.]+$/, '')}.jpg`;
    let edge = Math.min(longest, LIMITS.uploadMaxEdge);
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const last = edge <= LIMITS.uploadMinEdge;
      const blob = await canvasToBlob(drawScaled(source, edge), 'image/jpeg', last ? 0.8 : 0.9);
      if (blob && blob.size <= LIMITS.maxUploadBytes) {
        item.uploadImage = { blob, name };
        return item.uploadImage;
      }
      if (!blob || last) break;
      // A JPEG grows with its pixel count, so shrink the edge by the square root of the overshoot.
      edge = Math.max(LIMITS.uploadMinEdge, Math.floor(edge * Math.sqrt(LIMITS.maxUploadBytes / blob.size) * 0.95));
    }
  } finally {
    if (typeof source.close === 'function') source.close();
  }
  throw new ApiError(`"${item.name}" could not be shrunk below the ${formatBytes(LIMITS.maxUploadBytes)} upload limit.`, { kind: 'validation', service: 'Media host' });
}

/** 32-bit FNV-1a hash of a string (stable pseudo-randomness for Demo mode). */
export function hashString(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}
