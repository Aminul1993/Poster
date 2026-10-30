/** Timezone helpers (IANA zones via Intl; DST-aware). */

const formats = new Map();

/** Current IANA names for renamed zones: browsers still report "Asia/Calcutta" for India, for example. */
const RENAMED = Object.freeze({
  'Asia/Calcutta': 'Asia/Kolkata', 'Asia/Dacca': 'Asia/Dhaka', 'Asia/Katmandu': 'Asia/Kathmandu', 'Asia/Saigon': 'Asia/Ho_Chi_Minh',
  'Asia/Rangoon': 'Asia/Yangon', 'Asia/Thimbu': 'Asia/Thimphu', 'Europe/Kiev': 'Europe/Kyiv', 'America/Godthab': 'America/Nuuk'
});

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
    try { return this.modern(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'); } catch { return 'UTC'; }
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
    zones = [...new Set(zones.map((zone) => this.modern(zone)))];
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

  /** @param {string} zone @returns {string} the zone's current name ("Asia/Kolkata" for "Asia/Calcutta") */
  modern(zone) {
    return RENAMED[zone] || zone;
  },
  /** Whether `date` falls between 11 pm and 6 am in `zone`, when few people see a post. */
  isNight(date, zone) {
    const { hour } = this.parts(date, zone);
    return hour >= 23 || hour < 6;
  },
  /** Formats an instant as a datetime-local value in `zone`. */
  toLocalInput(date, zone) {
    const p = this.parts(date, zone);
    const pad = (n) => String(n).padStart(2, '0');
    return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
  },

  /**
   * Human readable date/time in `zone`, the one format the dashboard shows ("Fri, 30 Oct, 4:00 pm") whatever
   * the browser's locale, so it matches the "11 pm" and "6 am" written elsewhere. The year appears only when
   * it is not this year; the zone is named once beside the fields, not in every date.
   */
  format(date, zone) {
    try {
      const p = this.namedParts(date, zone);
      const otherYear = Number(p.year) !== this.parts(new Date(), zone).year;
      return `${p.weekday}, ${p.day} ${p.month}${otherYear ? ` ${p.year}` : ''}, ${this.clock(p)}`;
    } catch {
      return date.toISOString();
    }
  },

  /** @returns {string} the time of day alone in `zone` ("4:00 pm"); with seconds for the activity log */
  formatTime(date, zone, { seconds = false } = {}) {
    try {
      return this.clock(this.namedParts(date, zone), seconds);
    } catch {
      return date.toISOString().slice(11, seconds ? 19 : 16);
    }
  },

  /** Weekday, month and 12-hour clock parts of `date` in `zone`, in English. */
  namedParts(date, zone) {
    const dtf = dateTimeFormat('en-US', {
      timeZone: zone, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true
    });
    const out = {};
    for (const part of dtf.formatToParts(date)) if (part.type !== 'literal') out[part.type] = part.value;
    return out;
  },

  /** @returns {string} "4:00 pm", or "4:00:05 pm" */
  clock(p, seconds = false) {
    return `${p.hour}:${p.minute}${seconds ? `:${p.second}` : ''} ${String(p.dayPeriod).toLowerCase()}`;
  }
};
