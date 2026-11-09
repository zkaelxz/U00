/*
 * The Simple / Advanced view: a per-browser choice that decides whether the menu lists the admin and system pages.
 * It only changes what the menu offers; every page stays reachable by its link, and what a person may do is still
 * decided by the server.
 */
export type ViewMode = 'simple' | 'advanced'

export const VIEW_MODE_PREF = 'ui.mode'

export const asViewMode = (v: unknown): ViewMode => (v === 'advanced' ? 'advanced' : 'simple')
