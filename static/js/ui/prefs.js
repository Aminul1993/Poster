/** Interface preferences kept in this browser: filters, open panels and the image source (not settings). */

/**
 * imageCount and editCount: how many images Generate with AI and Edit with AI make per run.
 * @returns {{logFilter: string, plannerOpen: boolean, planInterval: number|null, imageSource: string, imageAspect: string, imageCount: number,
 *            editCount: number, galleryView: string, planTimeOfDay: string|null}}
 */
export function defaultPrefs() {
  return { logFilter: 'all', plannerOpen: false, planInterval: null, imageSource: 'upload', imageAspect: 'square', imageCount: 1, editCount: 1, galleryView: 'grid', planTimeOfDay: null };
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
  for (const key of ['imageCount', 'editCount']) {
    if (Number.isInteger(stored[key]) && stored[key] >= 1 && stored[key] <= 10) prefs[key] = stored[key];
  }
  if (['grid', 'list'].includes(stored.galleryView)) prefs.galleryView = stored.galleryView;
  if (typeof stored.planTimeOfDay === 'string' && /^\d{2}:\d{2}$/.test(stored.planTimeOfDay)) prefs.planTimeOfDay = stored.planTimeOfDay;
  return prefs;
}
