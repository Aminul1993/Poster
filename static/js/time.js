/** Timezone helpers (IANA zones via Intl; DST-aware). */

const formats = new Map();

/**
 * A shared Intl.DateTimeFormat: building one costs far more than formatting with it, and the editor and the
 * posts list format dates on every keystroke and render. Throws like the constructor for an unknown zone.
 * @param {string|undefined} locale @param {Intl.DateTimeFormatOptions} options
 * @returns {Intl.DateTimeFormat}
 */
export function dateTimeFormat(locale, options) {
  const key = `${locale ?? ''}|${JSON.stringify(options)}`;
  let format = formats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(locale, options);
    formats.set(key, format);
  }
  return format;
}

export const TimeZoneUtil = {
  /** @returns {string} the browser's IANA zone */
  browserZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
  },

  /** @param {string} zone @returns {boolean} */
  isValid(zone) {
    if (!zone || typeof zone !== 'string') return false;
    try { dateTimeFormat('en-US', { timeZone: zone }); return true; } catch { return false; }
  },

  /** @returns {string[]} all zones the browser knows, or a curated fallback list */
  list() {
    let zones = [];
    try { if (typeof Intl.supportedValuesOf === 'function') zones = Intl.supportedValuesOf('timeZone'); } catch { zones = []; }
    if (!zones.length) {
      zones = ['UTC', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Istanbul', 'Africa/Cairo',
        'Africa/Lagos', 'Africa/Johannesburg', 'Asia/Dubai', 'Asia/Karachi', 'Asia/Kolkata', 'Asia/Dhaka', 'Asia/Bangkok',
        'Asia/Singapore', 'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Australia/Sydney', 'Pacific/Auckland',
        'America/Sao_Paulo', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
        'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto', 'America/Mexico_City'];
    }
    if (!zones.includes('UTC')) zones = ['UTC', ...zones];
    return zones;
  },

  /** Wall-clock parts of `date` in `zone`. */
  parts(date, zone) {
    const dtf = dateTimeFormat('en-US', {
      timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
    const out = {};
    for (const part of dtf.formatToParts(date)) if (part.type !== 'literal') out[part.type] = Number(part.value);
    out.hour %= 24;
    return out;
  },

  /** Offset of `zone` from UTC at instant `date`, in milliseconds. */
  offsetMs(date, zone) {
    const p = this.parts(date, zone);
    const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
    return asUtc - (date.getTime() - date.getMilliseconds());
  },

  /**
   * Converts a datetime-local value ("YYYY-MM-DDTHH:mm") interpreted in `zone`
   * to an instant. `exists` is false for wall times skipped by a DST change.
   * @param {string} local @param {string} zone
   * @returns {{date: Date, exists: boolean} | null}
   */
  zonedToUtc(local, zone) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(local || ''));
    if (!match) return null;
    const [year, month, day, hour, minute] = match.slice(1, 6).map(Number);
    if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59) return null;
    const wall = Date.UTC(year, month - 1, day, hour, minute);
    let ts = wall - this.offsetMs(new Date(wall), zone);
    ts = wall - this.offsetMs(new Date(ts), zone);
    const date = new Date(ts);
    const normalizedInput = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}`;
    return { date, exists: this.toLocalInput(date, zone) === normalizedInput };
  },

  /** Formats an instant as a datetime-local value in `zone`. */
  toLocalInput(date, zone) {
    const p = this.parts(date, zone);
    const pad = (n) => String(n).padStart(2, '0');
    return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
  },

  /** Human readable date/time in `zone`, including the zone abbreviation. */
  format(date, zone, options = {}) {
    try {
      return dateTimeFormat(undefined, {
        timeZone: zone, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', timeZoneName: 'short', ...options
      }).format(date);
    } catch {
      return date.toISOString();
    }
  }
};
