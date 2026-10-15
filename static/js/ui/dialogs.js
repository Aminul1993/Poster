/** The confirmation / choice modal. */
import { el } from '../util.js';
import { byId } from './dom.js';

export class Dialogs {
  /**
   * Shows a modal with custom choices.
   * @param {{title: string, message: string, choices: Array<{value: string, label: string, primary?: boolean, danger?: boolean, disabled?: boolean}>,
   *          variant?: 'default'|'danger', cancelLabel?: string}} options
   * @returns {Promise<string|null>} chosen value, or null when cancelled
   */
  choose({ title, message, choices, variant = 'default', cancelLabel = 'Cancel' }) {
    const dialog = byId('confirm-dialog');
    if (dialog.open) return Promise.resolve(null);
    return new Promise((resolve) => {
      dialog.dataset.variant = variant;
      byId('confirm-title').textContent = title;
      byId('confirm-message').textContent = message;
      const buttons = choices.map((choice) => el('button', {
        type: 'submit', value: choice.value, disabled: Boolean(choice.disabled),
        class: `btn${choice.danger ? ' btn-danger' : choice.primary ? ' btn-primary' : ''}`, text: choice.label
      }));
      byId('confirm-actions').replaceChildren(el('button', { type: 'submit', value: '', class: 'btn', text: cancelLabel }), ...buttons);
      dialog.returnValue = '';
      const onClose = () => {
        dialog.removeEventListener('close', onClose);
        resolve(dialog.returnValue || null);
      };
      dialog.addEventListener('close', onClose);
      dialog.showModal();
      (buttons.find((b) => !b.disabled && b.classList.contains('btn-primary')) || buttons.find((b) => !b.disabled))?.focus();
    });
  }

  /**
   * @param {{title: string, message: string, confirmLabel?: string, danger?: boolean}} options
   * @returns {Promise<boolean>}
   */
  async confirm({ title, message, confirmLabel = 'Confirm', danger = false }) {
    const value = await this.choose({
      title, message, variant: danger ? 'danger' : 'default',
      choices: [{ value: 'confirm', label: confirmLabel, primary: !danger, danger }]
    });
    return value === 'confirm';
  }
}
