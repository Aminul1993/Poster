/**
 * Settings → Credentials & Integrations: the providers and credentials of text AI, image AI, blob storage and
 * Facebook & Instagram. The server describes each provider's fields, so a provider added on the server shows
 * up here with no change to this file. Secrets only ever arrive masked; a secret field left blank keeps the
 * one the server has.
 */
import { connectAccounts } from '../app.js';
import { INTEGRATION_SLUGS, settingsPayload, validateSettings } from '../services/integrations.js';
import { serviceMeta } from '../services/social.js';
import { el, icon, pluralize, relativeTime } from '../util.js';
import { byId, setFieldError } from './dom.js';
import { View } from './view.js';

export const INTEGRATION_TYPES = Object.freeze(['text_ai', 'image_ai', 'storage', 'meta']);

/** How each status reads, and its chip colour. */
export const STATUS = Object.freeze({
  connected: { label: 'Connected', chip: 'success' },
  error: { label: 'Error', chip: 'danger' },
  untested: { label: 'Not tested', chip: 'neutral' },
  not_configured: { label: 'Not configured', chip: 'warning' }
});

/**
 * A provider's status as the badge shows it: unconfigured comes before the last test.
 * @param {{configured: boolean, status: string}} provider @returns {{label: string, chip: string, key: string}}
 */
export function providerStatus(provider) {
  const key = !provider.configured ? 'not_configured' : (STATUS[provider.status] ? provider.status : 'untested');
  return { ...STATUS[key], key };
}

/** @param {string|null} stamp an ISO time @returns {string} "5 minutes ago", or "" */
function when(stamp) {
  if (!stamp) return '';
  const date = new Date(stamp);
  return Number.isNaN(date.getTime()) ? '' : relativeTime(date);
}

export class IntegrationsView extends View {
  constructor(ui) {
    super(ui);
    /** @type {object|null} the server's description of every integration (GET /api/settings/integrations) */
    this.data = null;
    /** The provider chosen in each section's dropdown (the active one until changed). */
    this.selected = {};
    /** Each section's form as last rendered, to tell unsaved changes. */
    this.snapshots = {};
    /** @type {{pages: object[], accounts: object[]}|null} what the last Meta test found */
    this.pages = null;
  }

  get service() { return this.app.integrations; }

  /** @param {string} type @returns {HTMLFormElement} */
  form(type) { return byId(`int-${INTEGRATION_SLUGS[type]}`); }

  /** @param {string} type @param {string} role @returns {any} */
  part(type, role) { return this.form(type).querySelector(`[data-role="${role}"]`); }

  fieldId(type, key) { return `int-${INTEGRATION_SLUGS[type]}-${key.replace(/_/g, '-')}`; }

  /** @param {string} type @returns {object|null} the provider chosen in the section */
  provider(type) {
    const kind = this.data?.integrations?.[type];
    return kind?.providers.find((p) => p.name === this.selected[type]) || null;
  }

  bind() {
    const dialog = byId('integrations-dialog');
    byId('open-integrations').addEventListener('click', () => this.open());
    byId('close-integrations').addEventListener('click', () => this.requestClose());
    byId('done-integrations').addEventListener('click', () => this.requestClose());
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); this.requestClose(); });
    byId('integrations-refresh').addEventListener('click', () => this.load());
    byId('integrations-admin').addEventListener('submit', (event) => { event.preventDefault(); this.unlock(); });
    for (const type of INTEGRATION_TYPES) {
      const form = this.form(type);
      form.addEventListener('submit', (event) => { event.preventDefault(); this.save(type); });
      form.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-action]');
        if (!button || button.type === 'submit') return;
        if (button.dataset.action === 'models') this.fetchModels(type, button);
        else this.test(type, button, { reconnect: button.dataset.action === 'reconnect' });
      });
      this.part(type, 'provider').addEventListener('change', (event) => {
        this.selected[type] = event.target.value;
        this.renderSection(type);
        this.updateFooter();
      });
      form.addEventListener('input', () => this.updateFooter());
      form.addEventListener('change', () => this.updateFooter());
    }
  }

  /** Opens the drawer (on top of Settings, when it is open) and loads the integrations. */
  async open() {
    const dialog = byId('integrations-dialog');
    if (!dialog.open) {
      this.pages = null;
      dialog.showModal();
    }
    byId('close-integrations').focus();
    await this.load();
  }

  async load() {
    this.setFooter('Loading…');
    try {
      const [data, status] = await Promise.all([this.service.load(), this.service.status()]);
      this.data = data;
      byId('integrations-admin').hidden = true;
      for (const type of INTEGRATION_TYPES) this.selected[type] = data.integrations[type].active;
      this.renderAll();
      this.renderHealth(status.services);
      this.setFooter('');
    } catch (error) {
      this.showLoadError(error);
    }
  }

  /** The server refused or failed: the administrator password, or a message in place of the sections. */
  showLoadError(error) {
    byId('integrations-sections').hidden = true;
    if (error.admin) {
      const form = byId('integrations-admin');
      form.hidden = false;
      setFieldError('integrations-admin-password', this.service.adminPassword ? error.message : null);
      this.notice('', '');
      this.setFooter('');
      byId('integrations-admin-password').focus();
      return;
    }
    this.notice('Credentials & Integrations could not be loaded', [error.message, error.hint].filter(Boolean).join(' '));
    this.setFooter('');
  }

  async unlock() {
    const input = byId('integrations-admin-password');
    if (!input.value) {
      setFieldError('integrations-admin-password', 'Enter the administrator password.');
      input.focus();
      return;
    }
    this.service.adminPassword = input.value;
    input.value = '';
    await this.load();
  }

  notice(title, text) {
    byId('integrations-notice').hidden = !title;
    byId('integrations-notice-title').textContent = title;
    byId('integrations-notice-text').textContent = text;
  }

  setFooter(text) { byId('integrations-footer').textContent = text; }

  /* ------------------------------------------------------------------ rendering */

  renderAll() {
    const data = this.data;
    byId('integrations-sections').hidden = false;
    if (!data.editable) {
      this.notice('Credentials are read-only here', `${data.editableProblem} Until then, Poster uses the environment variables, and you can still test them.`);
    } else if (data.database && !data.database.configured) {
      this.notice('The saved settings could not be read', data.database.problem || '');
    } else {
      this.notice('', '');
    }
    for (const type of INTEGRATION_TYPES) this.renderSection(type);
    this.updateFooter();
  }

  /** The section of one integration: provider dropdown, its fields, status and actions. */
  renderSection(type) {
    const kind = this.data.integrations[type];
    const provider = this.provider(type);
    if (!provider) return;
    const select = this.part(type, 'provider');
    select.replaceChildren(...kind.providers.map((p) => el('option', { value: p.name, text: p.name === kind.active ? `${p.label} (active)` : p.label })));
    select.value = provider.name;
    this.part(type, 'provider-field').hidden = kind.providers.length < 2;
    this.renderProviderNote(type, kind, provider);
    this.part(type, 'fields').replaceChildren(...provider.fields.filter((f) => f.kind !== 'pages').map((f) => this.renderField(type, provider, f)));
    const models = this.form(type).querySelector('[data-action="models"]');
    if (models) models.hidden = type !== 'text_ai';
    this.form(type).querySelector('[data-action="save"]').disabled = !this.data.editable;
    if (type === 'meta') this.renderPages();
    this.renderStatus(type, provider);
    this.part(type, 'result').hidden = true;
    this.snapshots[type] = this.snapshot(type);
  }

  renderProviderNote(type, kind, provider) {
    const note = this.part(type, 'provider-note');
    const active = kind.providers.find((p) => p.name === kind.active);
    let text;
    if (provider.name !== kind.active) text = `Saving makes ${provider.label} the active provider in place of ${active?.label || kind.active}, from the next request on.`;
    else if (provider.source === 'admin') text = 'Active · saved in this dashboard (encrypted).';
    else if (provider.source === 'env') text = 'Active · imported from the environment, which stays in charge until you save here.';
    else text = 'Active · configured by environment variables.';
    const children = [text];
    if (provider.docsUrl) children.push(' ', el('a', { href: provider.docsUrl, target: '_blank', rel: 'noopener noreferrer', text: 'Get a key' }));
    note.replaceChildren(...children);
  }

  /** One field, built from the server's description of it. */
  renderField(type, provider, field) {
    const id = this.fieldId(type, field.key);
    const secret = field.kind === 'secret';
    const readonly = !this.data.editable;
    let placeholder = field.placeholder || (field.default ? `Default: ${field.default}` : '');
    if (secret) placeholder = field.hasValue ? `${field.masked} · leave blank to keep` : (field.placeholder || 'Not set');
    const attrs = {
      class: 'input', id, name: field.key, type: secret ? 'password' : 'text', autocomplete: secret ? 'new-password' : 'off',
      spellcheck: 'false', placeholder, readonly, 'aria-describedby': `${id}-hint ${id}-error`,
      dataset: { key: field.key, kind: field.kind }
    };
    if (field.kind === 'url') attrs.inputmode = 'url';
    let input;
    if (field.kind === 'select') {
      input = el('select', { class: 'select', id, name: field.key, disabled: readonly, dataset: { key: field.key, kind: field.kind } },
        field.options.map((option) => el('option', { value: option, text: option })));
      input.value = field.value || field.default || field.options[0] || '';
    } else {
      input = el('input', attrs);
      if (!secret) input.value = field.value || '';
    }
    const nodes = [input];
    if (type === 'text_ai' && field.key === 'default_model') {
      input.setAttribute('list', `${id}-options`);
      nodes.push(el('datalist', { id: `${id}-options` }));
    }
    return el('div', { class: 'field' }, [
      el('label', { class: 'label', for: id }, [field.label, field.required ? null : el('span', { class: 'optional', text: 'optional' })]),
      ...nodes,
      el('p', { class: 'hint', id: `${id}-hint`, text: this.fieldHint(field) }),
      el('p', { class: 'error-text', id: `${id}-error`, hidden: true })
    ]);
  }

  /** Where a field's value comes from, after its help text. */
  fieldHint(field) {
    let origin = '';
    if (field.kind === 'secret') {
      origin = !field.hasValue ? 'Not set.'
        : field.origin === 'saved' ? `Saved, encrypted: ${field.masked}.`
          : `From ${field.env || 'the environment'}: ${field.masked}.`;
    } else if (field.origin === 'env' && field.env) {
      origin = `From ${field.env}.`;
    }
    return [field.help, origin].filter(Boolean).join(' ');
  }

  /** The status badge, and when the provider was last tested and last connected. */
  renderStatus(type, provider) {
    const status = providerStatus(provider);
    const badge = this.part(type, 'badge');
    badge.className = `chip chip-${status.chip}`;
    badge.textContent = status.label;
    badge.title = provider.problem || provider.error || '';
    const parts = [];
    if (!provider.configured && provider.problem) parts.push(provider.problem);
    if (provider.lastTestedAt) parts.push(`Last tested ${when(provider.lastTestedAt)}`);
    if (provider.lastSuccessfulConnection) parts.push(`${type === 'meta' ? 'Last successful sync' : 'Last successful connection'} ${when(provider.lastSuccessfulConnection)}`);
    if (provider.status === 'error' && provider.error) parts.push(`Last error: ${provider.error}`);
    this.part(type, 'times').textContent = parts.join(' · ');
  }

  /** The Pages and Instagram accounts the last Meta test found; the ticked Pages are the ones Poster may use. */
  renderPages() {
    const box = this.part('meta', 'pages');
    if (!this.pages) { box.hidden = true; return; }
    box.hidden = false;
    const chosen = new Set(this.savedPageIds());
    const list = this.part('meta', 'page-list');
    list.replaceChildren(...this.pages.pages.map((page) => {
      const meta = serviceMeta('facebook');
      return el('label', { class: `channel${page.usable ? '' : ' is-disabled'}` }, [
        el('input', { type: 'checkbox', checked: chosen.has(page.id), disabled: !this.data.editable, dataset: { pageId: page.id }, 'aria-label': page.name }),
        el('span', { class: 'channel-avatar', style: `background:color-mix(in srgb, ${meta.color} 76%, #000)`, 'aria-hidden': 'true', text: page.name.charAt(0) }),
        el('span', { class: 'channel-info' }, [
          el('span', { class: 'channel-name', style: 'display:block', text: page.name }),
          el('span', { class: `channel-sub${page.usable ? '' : ' error-text'}`, style: 'display:block', text: page.usable ? `Page ${page.id}` : page.problem })
        ])
      ]);
    }));
    const accounts = this.pages.accounts;
    this.part('meta', 'accounts').replaceChildren(...(accounts.length
      ? accounts.map((a) => el('li', { text: `${a.name}${a.handle ? ` (@${a.handle})` : ''} · linked to ${a.pageName}` }))
      : [el('li', { class: 'hint', text: 'No Instagram professional account is linked to these Pages.' })]));
    this.updatePageCount();
  }

  updatePageCount() {
    const count = this.part('meta', 'page-list').querySelectorAll('input:checked').length;
    this.part('meta', 'pages-count').textContent = count ? `${count} chosen` : 'All Pages';
  }

  /** @returns {string[]} the Pages saved as chosen */
  savedPageIds() {
    const field = this.provider('meta')?.fields.find((f) => f.key === 'page_ids');
    return String(field?.value || '').split(',').map((v) => v.trim()).filter(Boolean);
  }

  /** @param {Array<object>} lines GET /api/settings/status's services */
  renderHealth(lines) {
    const rows = lines.map((line) => {
      const status = STATUS[line.status] || STATUS.untested;
      return el('tr', { dataset: { type: line.type } }, [
        el('th', { scope: 'row', text: line.service }),
        el('td', { text: line.providerLabel }),
        el('td', {}, [el('span', { class: `chip chip-${status.chip}`, text: status.label, title: line.problem || line.error || '' })]),
        el('td', { text: when(line.lastTestedAt) || '—' })
      ]);
    });
    byId('integrations-health').tBodies[0].replaceChildren(...rows);
  }

  async refreshHealth() {
    try {
      this.renderHealth((await this.service.status()).services);
    } catch { /* the table keeps what it showed */ }
  }

  /* -------------------------------------------------------------- the form */

  /** @returns {Object<string, string|string[]>} the section's values, by field key */
  read(type) {
    const values = {};
    for (const input of this.part(type, 'fields').querySelectorAll('[data-key]')) values[input.dataset.key] = input.value;
    if (type === 'meta') {
      values.page_ids = this.pages
        ? [...this.part('meta', 'page-list').querySelectorAll('input[data-page-id]:checked')].map((i) => i.dataset.pageId)
        : this.savedPageIds();
    }
    return values;
  }

  snapshot(type) { return JSON.stringify([this.selected[type], this.read(type)]); }

  isDirty(type) {
    return Boolean(this.data) && this.snapshots[type] !== undefined && this.snapshot(type) !== this.snapshots[type];
  }

  updateFooter() {
    if (!this.data) return;
    if (this.pages) this.updatePageCount();
    const dirty = INTEGRATION_TYPES.filter((t) => this.isDirty(t)).map((t) => this.data.integrations[t].label);
    this.setFooter(dirty.length ? `Unsaved changes: ${dirty.join(', ')}` : '');
  }

  /** Shows the browser's own checks; @returns {boolean} whether the form may be sent */
  check(type) {
    const provider = this.provider(type);
    const errors = validateSettings(provider.fields, this.read(type));
    for (const field of provider.fields) if (field.kind !== 'pages') setFieldError(this.fieldId(type, field.key), errors[field.key] || null);
    const first = Object.keys(errors)[0];
    if (first) {
      this.showResult(type, 'error', errors[first]);
      byId(this.fieldId(type, first))?.focus();
      return false;
    }
    return true;
  }

  /** @param {'ok'|'error'|'info'} kind */
  showResult(type, kind, text) {
    const box = this.part(type, 'result');
    box.hidden = false;
    box.className = `test-result is-${kind}`;
    box.replaceChildren(icon(kind === 'ok' ? 'check-circle' : kind === 'error' ? 'alert-circle' : 'info'), el('span', { text }));
  }

  setLoading(button, loading) {
    button.classList.toggle('is-loading', loading);
    button.disabled = loading;
    button.setAttribute('aria-busy', String(loading));
  }

  /** A refusal for want of the administrator password reopens the prompt; anything else is said in the section. */
  fail(type, error) {
    if (error.admin) { this.showLoadError(error); return; }
    this.showResult(type, 'error', [error.message, error.hint].filter(Boolean).join(' '));
  }

  /* --------------------------------------------------------------- actions */

  async save(type) {
    const provider = this.provider(type);
    if (!provider || !this.check(type)) return;
    const button = this.form(type).querySelector('[data-action="save"]');
    const kind = this.data.integrations[type];
    const switching = provider.name !== kind.active;
    this.setLoading(button, true);
    this.showResult(type, 'info', 'Saving…');
    try {
      const result = await this.service.save(type, provider.name, settingsPayload(provider.fields, this.read(type)));
      this.data.integrations[type] = result.integration;
      this.selected[type] = result.integration.active;
      this.renderSection(type);
      this.renderHealth(result.status);
      this.showResult(type, 'ok', switching
        ? `Saved. ${provider.label} is now the active ${kind.label} provider.`
        : 'Saved. The new settings apply from the next request.');
      this.updateFooter();
      await this.afterChange();
    } catch (error) {
      this.fail(type, error);
    } finally {
      this.setLoading(button, false);
      button.disabled = !this.data?.editable;
    }
  }

  /**
   * Tests the section's provider: with the form's settings when they differ from the saved ones (nothing is
   * saved), else the saved settings (whose status the server then records). Reconnect also reloads the
   * dashboard's Facebook and Instagram channels.
   */
  async test(type, button, { reconnect = false } = {}) {
    const provider = this.provider(type);
    if (!provider || !this.check(type)) return;
    const unsaved = this.isDirty(type) || provider.name !== this.data.integrations[type].active;
    this.setLoading(button, true);
    this.showResult(type, 'info', type === 'storage' ? 'Validating the storage…' : 'Testing the connection…');
    try {
      const result = await this.service.test(type, provider.name, unsaved ? settingsPayload(provider.fields, this.read(type)) : null);
      if (result.integration) {
        Object.assign(provider, { status: result.integration.status, lastTestedAt: result.integration.lastTestedAt,
          lastSuccessfulConnection: result.integration.lastSuccessfulConnection, error: result.integration.error });
        this.renderStatus(type, provider);
      }
      if (type === 'meta' && result.pages) {
        const values = this.read(type);
        this.pages = { pages: result.pages, accounts: result.instagramAccounts || [] };
        this.renderPages();
        if (!unsaved) this.snapshots[type] = this.snapshot(type);
        else this.restorePageSelection(values.page_ids);
      }
      let detail = result.detail || 'Connected.';
      if (unsaved) detail += ' (Unsaved settings: save them to use them.)';
      if (reconnect && result.ok && !unsaved) {
        const connection = await connectAccounts({ mode: 'live' });
        detail += ` The dashboard's channels were reloaded: ${pluralize(connection.channels.length, 'channel')}.`;
        this.ui.health.social = { state: 'ok' };
        this.ui.renderHeader();
      }
      this.showResult(type, result.ok ? 'ok' : 'error', detail);
      this.refreshHealth();
      this.app.activity.load();
    } catch (error) {
      this.fail(type, error);
      this.refreshHealth();
    } finally {
      this.setLoading(button, false);
    }
  }

  restorePageSelection(ids) {
    const chosen = new Set(ids);
    for (const input of this.part('meta', 'page-list').querySelectorAll('input[data-page-id]')) input.checked = chosen.has(input.dataset.pageId);
    this.updatePageCount();
  }

  async fetchModels(type, button) {
    const provider = this.provider(type);
    if (!provider || !this.check(type)) return;
    this.setLoading(button, true);
    this.showResult(type, 'info', 'Fetching the models…');
    try {
      const { models } = await this.service.models(provider.name, settingsPayload(provider.fields, this.read(type)));
      const list = byId(`${this.fieldId(type, 'default_model')}-options`);
      list?.replaceChildren(...models.map((model) => el('option', { value: model })));
      this.showResult(type, models.length ? 'ok' : 'info', models.length
        ? `${pluralize(models.length, 'model')} available. Pick one in the model field, then save.`
        : `${provider.label} does not list its models here; type the model name.`);
      if (models.length) byId(this.fieldId(type, 'default_model'))?.focus();
    } catch (error) {
      this.fail(type, error);
    } finally {
      this.setLoading(button, false);
    }
  }

  /** The dashboard follows a saved change at once: the server's configuration, the header, Settings and banners. */
  async afterChange() {
    try {
      await this.service.refreshServer();
    } catch (error) {
      this.setFooter(`Saved, but the dashboard could not reload the server's configuration: ${error.message}`);
      return;
    }
    this.ui.onIntegrationsChanged();
    this.app.activity.load();
  }

  async requestClose() {
    const dirty = INTEGRATION_TYPES.filter((t) => this.isDirty(t));
    if (dirty.length) {
      const ok = await this.ui.confirm({
        title: 'Discard unsaved changes?',
        message: `Your changes to ${dirty.map((t) => this.data.integrations[t].label).join(', ')} have not been saved.`,
        confirmLabel: 'Discard', danger: true
      });
      if (!ok) return;
    }
    byId('integrations-dialog').close();
    const opener = byId('open-integrations');
    if (byId('settings-dialog').open) opener.focus();
  }
}
