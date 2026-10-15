/** Starts generation and scheduling runs: pre-flight checks, confirmations, then SchedulerService. */
import { showToast } from '../app.js';
import { hasContent } from '../store.js';
import { pluralize } from '../util.js';
import { byId } from './dom.js';
import { View } from './view.js';

export class Orchestrator extends View {
  /** Common pre-flight checks for live operations. @returns {boolean} */
  ensureOnline() {
    if (this.settings.mode === 'live' && navigator.onLine === false) {
      showToast('You are offline. Reconnect, or switch to Demo mode.', 'error', { title: 'No connection' });
      return false;
    }
    return true;
  }

  /**
   * Starts content generation for the given images.
   * @param {string[]} ids @param {'single'|'selected'|'all'} scope
   * @returns {Promise<import('../scheduler.js').Operation|null>}
   */
  async startGeneration(ids, scope) {
    const items = ids.map((id) => this.store.get(id)).filter(Boolean);
    if (!items.length) {
      showToast(scope === 'selected' ? 'Select images in the gallery first.' : 'Upload an image first.', 'info');
      return null;
    }
    if (this.scheduler.isRunning) {
      showToast('Another operation is running. Wait for it to finish or cancel it.', 'warning');
      return null;
    }
    const settings = this.settings;
    if (settings.mode === 'live') {
      const check = this.validation.validateSettings(settings, { scope: 'ollama', requireCredentials: true });
      if (!check.valid) {
        showToast(Object.values(check.errors)[0], 'error', { title: 'Ollama is not configured' });
        this.ui.openSettings('settings-ollama');
        return null;
      }
      if (!this.ensureOnline()) return null;
    }
    let targets = items;
    const withContent = items.filter(hasContent);
    if (withContent.length) {
      if (items.length === 1) {
        const item = items[0];
        const ok = await this.ui.confirm({
          title: 'Replace the existing content?',
          message: `"${item.name}" already has a caption${item.result ? ' and has been scheduled' : ''}. Generating again replaces the captions, hashtags, category and score, including your edits.${item.result ? ' The post already in Buffer is not changed.' : ''}`,
          confirmLabel: 'Regenerate'
        });
        if (!ok) return null;
      } else {
        const fresh = items.length - withContent.length;
        const choice = await this.ui.choose({
          title: 'Some images already have content',
          message: `${pluralize(withContent.length, 'image')} of ${items.length} already ${withContent.length === 1 ? 'has' : 'have'} content. Regenerating replaces it, including your edits.`,
          choices: [
            { value: 'new', label: `Only new (${fresh})`, disabled: fresh === 0 },
            { value: 'all', label: `Regenerate all ${items.length}`, primary: true }
          ]
        });
        if (!choice) return null;
        if (choice === 'new') targets = items.filter((i) => !hasContent(i));
      }
    }
    if (!targets.length) return null;
    const label = `Content generation for ${pluralize(targets.length, 'image')}`;
    this.notifier.log('info', 'user', `Started ${label.toLowerCase()} (${settings.mode} mode).`);
    return this.scheduler.run({
      kind: 'generate',
      label,
      items: targets,
      concurrency: settings.mode === 'demo' ? 3 : settings.aiConcurrency,
      worker: (item, ctx) => this.scheduler.generateItem(item, ctx)
    });
  }

  /**
   * Starts scheduling for the given images.
   * @param {string[]} ids @param {'single'|'selected'|'all'} scope
   * @returns {Promise<import('../scheduler.js').Operation|null>}
   */
  async startScheduling(ids, scope) {
    const items = ids.map((id) => this.store.get(id)).filter(Boolean);
    if (!items.length) {
      showToast(scope === 'selected' ? 'Select images in the gallery first.' : scope === 'all' ? 'No posts are ready. Generate or write captions first.' : 'Upload an image first.', 'info');
      return null;
    }
    if (this.scheduler.isRunning) {
      showToast('Another operation is running. Wait for it to finish or cancel it.', 'warning');
      return null;
    }
    const settings = this.settings;
    const buffer = this.app.getBuffer();
    if (settings.mode === 'live') {
      const check = this.validation.validateSettings(settings, { scope: 'buffer', requireCredentials: true });
      if (!check.valid) {
        showToast(Object.values(check.errors)[0], 'error', { title: 'Buffer is not configured' });
        this.ui.openSettings('settings-buffer');
        return null;
      }
      if (!this.ensureOnline()) return null;
    }
    const channelIds = buffer.selectedChannelIds(settings);
    if (!channelIds.length) {
      showToast('Choose at least one channel to post to.', 'error', { title: 'No Buffer channels selected' });
      this.ui.openSettings('settings-buffer');
      return null;
    }
    const channelProblem = buffer.checkChannels(channelIds, settings);
    if (channelProblem) {
      showToast(channelProblem, 'error', { title: 'Channel problem' });
      this.ui.openSettings('settings-buffer');
      return null;
    }

    let targets = items;
    let skipNote = '';
    if (scope === 'single') {
      const item = items[0];
      const check = this.validation.validatePost(item, settings);
      if (!check.valid) {
        this.store.update(item.id, { showErrors: true }, { reason: 'validation', persist: false });
        showToast(check.message, 'error', { title: 'Fix the post before scheduling' });
        const fieldMap = { description: 'ed-description', alternative_description: 'ed-alt', hashtags: 'ed-tag-input', category: 'ed-category', schedule: 'ed-schedule', mediaUrl: 'ed-media-url' };
        byId(fieldMap[Object.keys(check.errors)[0]])?.focus();
        return null;
      }
      if (item.result) {
        const ok = await this.ui.confirm({ title: 'Schedule this post again?', message: `"${item.name}" was already scheduled. Scheduling again creates another post in Buffer.`, confirmLabel: 'Schedule again' });
        if (!ok) return null;
      }
    } else {
      targets = items.filter((i) => !i.result);
      const already = items.length - targets.length;
      if (!targets.length) {
        showToast('These posts are already scheduled.', 'info');
        return null;
      }
      if (already) skipNote = ` ${pluralize(already, 'post')} already scheduled will be skipped.`;
    }

    const channelNames = channelIds.map((id) => buffer.channelName(id, settings)).join(', ');
    if (settings.mode === 'live') {
      const ok = await this.ui.confirm({
        title: `Schedule ${pluralize(targets.length, 'post')} in Buffer?`,
        message: `Each post goes to ${channelNames} at its publish time (${settings.timezone}).${skipNote}`,
        confirmLabel: `Schedule ${pluralize(targets.length, 'post')}`
      });
      if (!ok) return null;
    }
    const label = `Scheduling of ${pluralize(targets.length, 'post')}`;
    this.notifier.log('info', 'user', `Started ${label.toLowerCase()} to ${channelNames} (${settings.mode} mode).`);
    return this.scheduler.run({
      kind: 'schedule',
      label,
      items: targets,
      concurrency: 1,
      worker: (item, ctx) => this.scheduler.scheduleItem(item, ctx, { skipScheduled: scope !== 'single' })
    });
  }
}
