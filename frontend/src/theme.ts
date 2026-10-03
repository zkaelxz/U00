/*
 * Theme preference: follow the system, or light, dark, sepia or oled.
 * Client-side only, per browser: stored in localStorage and applied as
 * <html data-theme="light|dark|sepia|oled">; "system" removes the attribute so the
 * prefers-color-scheme tokens in index.css apply. Storage may throw
 * (private window, blocked site data); every access is wrapped and the
 * page then just follows the system.
 *
 * setThemePref is the one way to change it (the Settings select and the
 * header menu both call it), so the two always agree.
 */
import { useSyncExternalStore } from 'react'

export type ThemePref = 'system' | 'light' | 'dark' | 'sepia' | 'oled'
/** The look a theme resolves to once "system" is settled. */
export type ThemeLook = 'light' | 'dark' | 'sepia' | 'oled'

export const THEME_KEY = 'baihe.theme'
export const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: 'system', label: 'Match this device' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'sepia', label: 'Sepia' },
  { value: 'oled', label: 'OLED black' },
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
  return raw === 'light' || raw === 'dark' || raw === 'sepia' || raw === 'oled' ? raw : 'system'
}

export function themeLabel(pref: ThemePref): string {
  return THEME_OPTIONS.find((o) => o.value === pref)?.label ?? ''
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

/** The look in effect: the saved choice, or the system's light/dark when "system". */
export function resolveTheme(pref: ThemePref, systemDark: boolean): ThemeLook {
  return pref === 'system' ? (systemDark ? 'dark' : 'light') : pref
}

export function applyTheme(pref: ThemePref, root: HTMLElement | null = globalThis.document?.documentElement ?? null): void {
  if (!root) return
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}

// Shared state so every control shows the same choice at once.
let current: ThemePref | null = null
const listeners = new Set<() => void>()

function emit(next: ThemePref): void {
  if (next === current) return
  current = next
  listeners.forEach((l) => l())
}

export function getThemePref(): ThemePref {
  current ??= loadTheme()
  return current
}

/** Save, apply and announce a new choice. */
export function setThemePref(pref: ThemePref): void {
  saveTheme(pref)
  applyTheme(pref)
  emit(pref)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  // Another tab changed the saved choice.
  const onStorage = (e: StorageEvent) => {
    if (e.key !== null && e.key !== THEME_KEY) return
    const next = loadTheme()
    applyTheme(next)
    emit(next)
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

export function useThemePref(): ThemePref {
  return useSyncExternalStore(subscribe, getThemePref, () => 'system')
}

/** Forget the shared state so the next read reloads from storage (tests). */
export function resetThemeState(): void {
  current = null
}
