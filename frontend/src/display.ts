/*
 * Display preferences kept per browser: how wide the page column is, how large the text is and how tight the spacing is.
 * Stored in localStorage and applied as <html data-width data-text data-density>; the default of each removes the
 * attribute so index.css's own values apply. Storage may throw (private window); the page then keeps the defaults.
 * index.html repeats the read before first paint, so keep its keys and values in step with this file.
 */
import { useSyncExternalStore } from 'react'
import { browserStorage } from './hooks/usePersistedState'

export type PageWidth = 'standard' | 'wide' | 'full'
export type TextSize = 'small' | 'default' | 'large' | 'larger'
export type Density = 'comfortable' | 'compact'

export const WIDTH_KEY = 'baihe.width'
export const TEXT_KEY = 'baihe.textSize'
export const DENSITY_KEY = 'baihe.density'

export const WIDTH_OPTIONS: { value: PageWidth; label: string }[] = [
  { value: 'standard', label: 'Standard (1440 px)' },
  { value: 'wide', label: 'Wide (1800 px)' },
  { value: 'full', label: 'Full window' },
]
export const TEXT_OPTIONS: { value: TextSize; label: string }[] = [
  { value: 'small', label: 'Small' },
  { value: 'default', label: 'Default' },
  { value: 'large', label: 'Large' },
  { value: 'larger', label: 'Larger' },
]

export const DENSITY_OPTIONS: { value: Density; label: string }[] = [
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'compact', label: 'Compact' },
]

export type Display = { width: PageWidth; text: TextSize; density: Density }
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function parseWidth(raw: unknown): PageWidth {
  return raw === 'wide' || raw === 'full' ? raw : 'standard'
}

export function parseTextSize(raw: unknown): TextSize {
  return raw === 'small' || raw === 'large' || raw === 'larger' ? raw : 'default'
}

export function parseDensity(raw: unknown): Density {
  return raw === 'compact' ? raw : 'comfortable'
}

export function loadDisplay(storage: Store | null = browserStorage()): Display {
  try {
    return { width: parseWidth(storage?.getItem(WIDTH_KEY)), text: parseTextSize(storage?.getItem(TEXT_KEY)), density: parseDensity(storage?.getItem(DENSITY_KEY)) }
  } catch {
    return { width: 'standard', text: 'default', density: 'comfortable' }
  }
}

export function applyDisplay(display: Display, root: HTMLElement | null = globalThis.document?.documentElement ?? null): void {
  if (!root) return
  if (display.width === 'standard') root.removeAttribute('data-width')
  else root.setAttribute('data-width', display.width)
  if (display.text === 'default') root.removeAttribute('data-text')
  else root.setAttribute('data-text', display.text)
  if (display.density === 'comfortable') root.removeAttribute('data-density')
  else root.setAttribute('data-density', display.density)
}

let current: Display | null = null
const listeners = new Set<() => void>()

function getDisplay(): Display {
  current ??= loadDisplay()
  return current
}

function emit(next: Display): void {
  if (next.width === current?.width && next.text === current?.text && next.density === current?.density) return
  current = next
  listeners.forEach((l) => l())
}

/** Save, apply and announce a change to one or both preferences. */
export function setDisplay(change: Partial<Display>): void {
  const next = { ...getDisplay(), ...change }
  const storage = browserStorage()
  try {
    if (next.width === 'standard') storage?.removeItem(WIDTH_KEY)
    else storage?.setItem(WIDTH_KEY, next.width)
    if (next.text === 'default') storage?.removeItem(TEXT_KEY)
    else storage?.setItem(TEXT_KEY, next.text)
    if (next.density === 'comfortable') storage?.removeItem(DENSITY_KEY)
    else storage?.setItem(DENSITY_KEY, next.density)
  } catch {
    // Not remembered; it still applies to this page.
  }
  applyDisplay(next)
  emit(next)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  // Another tab changed a saved choice.
  const onStorage = (e: StorageEvent) => {
    if (e.key !== null && e.key !== WIDTH_KEY && e.key !== TEXT_KEY && e.key !== DENSITY_KEY) return
    const next = loadDisplay()
    applyDisplay(next)
    emit(next)
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

const SERVER_DISPLAY: Display = { width: 'standard', text: 'default', density: 'comfortable' }

export function useDisplay(): Display {
  return useSyncExternalStore(subscribe, getDisplay, () => SERVER_DISPLAY)
}

/** Forget the shared state so the next read reloads from storage (tests). */
export function resetDisplayState(): void {
  current = null
}
