/** Interface preferences kept in this browser: filters, open panels and the image source (not settings). */

/** @returns {{logFilter: string, plannerOpen: boolean, planInterval: number|null, imageSource: string, imageAspect: string, galleryView: string, planTimeOfDay: string|null}} */
export function defaultPrefs() {
  return { logFilter: 'all', plannerOpen: false, planInterval: null, imageSource: 'upload', imageAspect: 'square', galleryView: 'grid', planTimeOfDay: null };
}

/** The stored preferences over the defaults; unknown or invalid values are ignored. */
export function normalizePrefs(stored) {
  const prefs = defaultPrefs();
  if (stored === null || typeof stored !== 'object' || Array.isArray(stored)) return prefs;
  if (['all', 'error', 'warn', 'success', 'user'].includes(stored.logFilter)) prefs.logFilter = stored.logFilter;
  prefs.plannerOpen = Boolean(stored.plannerOpen);
  if (Number.isInteger(stored.planInterval) && stored.planInterval > 0) prefs.planInterval = stored.planInterval;
  if (['upload', 'generate', 'edit'].includes(stored.imageSource)) prefs.imageSource = stored.imageSource;
  if (['square', 'portrait', 'landscape', 'story'].includes(stored.imageAspect)) prefs.imageAspect = stored.imageAspect;
  if (['grid', 'list'].includes(stored.galleryView)) prefs.galleryView = stored.galleryView;
  if (typeof stored.planTimeOfDay === 'string' && /^\d{2}:\d{2}$/.test(stored.planTimeOfDay)) prefs.planTimeOfDay = stored.planTimeOfDay;
  return prefs;
}
