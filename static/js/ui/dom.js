/** DOM helpers shared by the views. */
import { el } from '../util.js';

/** The next-step shortcut's modifier: ⌘ on Apple keyboards, Ctrl elsewhere (for aria-keyshortcuts and labels). */
export const MOD_KEY = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')
  ? Object.freeze({ aria: 'Meta', label: '⌘' })
  : Object.freeze({ aria: 'Control', label: 'Ctrl' });

/** @param {string} id @returns {any} */
export function byId(id) {
  return document.getElementById(id);
}

/**
 * Shows or hides an inline field error and sets aria-invalid.
 * @param {string} id input id (error element is `${id}-error`)
 * @param {string|null} message
 * @param {HTMLElement} [target] element that gets aria-invalid
 */
export function setFieldError(id, message, target = byId(id)) {
  const error = byId(`${id}-error`);
  if (error) {
    error.hidden = !message;
    error.textContent = message || '';
  }
  if (target) {
    if (message) target.setAttribute('aria-invalid', 'true');
    else target.removeAttribute('aria-invalid');
  }
}

/** @param {DragEvent} event @returns {boolean} whether the drag carries files */
export function hasFiles(event) {
  return Boolean(event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files'));
}

/** @param {{label: string, chip: string}} status @returns {HTMLElement} a status chip */
export function statusChip(status) {
  return el('span', { class: `chip chip-${status.chip}`, text: status.label });
}

/** @param {string} color @returns {HTMLElement} the small dot in a channel's colour */
export function colorDot(color) {
  return el('span', { style: `width:8px;height:8px;border-radius:50%;background:${color};flex:none` });
}
