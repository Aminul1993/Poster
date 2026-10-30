/** The confirmation / choice modal. */
import { el } from '../util.js';
import { byId } from './dom.js';

export class Dialogs {
  /**
   * Shows a modal with custom choices.
   * @param {{title: string, message: string, choices: Array<{value: string, label: string, primary?: boolean, danger?: boolean, disabled?: boolean}>,
   *          variant?: 'default'|'danger', cancelLabel?: string, symbol?: string, focusCancel?: boolean}} options
   * @returns {Promise<string|null>} chosen value, or null when cancelled
   */
  choose({ title, message, choices, variant = 'default', cancelLabel = 'Cancel', symbol = 'alert', focusCancel = false }) {
    const dialog = byId('confirm-dialog');
    if (dialog.open) return Promise.resolve(null);
    return new Promise((resolve) => {
      dialog.dataset.variant = variant;
      dialog.querySelector('.modal-icon use').setAttribute('href', `#i-${symbol}`);
      byId('confirm-title').textContent = title;
      byId('confirm-message').textContent = message;
      const buttons = choices.map((choice) => el('button', {
        type: 'submit', value: choice.value, disabled: Boolean(choice.disabled),
        class: `btn${choice.danger ? ' btn-danger' : choice.primary ? ' btn-primary' : ''}`, text: choice.label
      }));
      const cancel = el('button', { type: 'submit', value: '', class: 'btn', text: cancelLabel });
      byId('confirm-actions').replaceChildren(cancel, ...buttons);
      dialog.returnValue = '';
      const onClose = () => {
        dialog.removeEventListener('close', onClose);
        resolve(dialog.returnValue || null);
      };
      dialog.addEventListener('close', onClose);
      dialog.showModal();
      // Enter must never destroy anything: focus the safe primary choice, otherwise Cancel.
      ((!focusCancel && buttons.find((b) => !b.disabled && b.classList.contains('btn-primary'))) || cancel).focus();
    });
  }

  /**
   * @param {{title: string, message: string, confirmLabel?: string, danger?: boolean, symbol?: string, focusCancel?: boolean}} options
   * @returns {Promise<boolean>}
   */
  async confirm({ title, message, confirmLabel = 'Confirm', danger = false, symbol, focusCancel }) {
    const value = await this.choose({
      title, message, symbol, focusCancel, variant: danger ? 'danger' : 'default',
      choices: [{ value: 'confirm', label: confirmLabel, primary: !danger, danger }]
    });
    return value === 'confirm';
  }
}
