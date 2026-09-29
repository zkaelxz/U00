/*
 * Theme preference (inventory G03): light, dark or follow the system.
 * Client-side only, per browser: stored in localStorage and applied as
 * <html data-theme="light|dark">; "system" removes the attribute so the
 * prefers-color-scheme tokens in index.css apply. Storage may throw
 * (private window, blocked site data); every access is wrapped and the
 * page then just follows the system.
 */

export type ThemePref = 'system' | 'light' | 'dark'

export const THEME_KEY = 'baihe.theme'
export const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: 'system', label: 'Match this device' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

export type ThemeStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function browserStorage(): ThemeStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export function parseTheme(raw: unknown): ThemePref {
  return raw === 'light' || raw === 'dark' ? raw : 'system'
}

export function loadTheme(storage: ThemeStorage | null = browserStorage()): ThemePref {
  try {
    return parseTheme(storage?.getItem(THEME_KEY))
  } catch {
    return 'system'
  }
}

export function saveTheme(pref: ThemePref, storage: ThemeStorage | null = browserStorage()): void {
  try {
    if (pref === 'system') storage?.removeItem(THEME_KEY)
    else storage?.setItem(THEME_KEY, pref)
  } catch {
    // Not remembered; it still applies to this page.
  }
}

/** Whether the app is dark: the saved choice, or the system's when "system". */
export function isDarkTheme(pref: ThemePref, systemDark: boolean): boolean {
  return pref === 'system' ? systemDark : pref === 'dark'
}

export function applyTheme(pref: ThemePref, root: HTMLElement | null = globalThis.document?.documentElement ?? null): void {
  if (!root) return
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}
