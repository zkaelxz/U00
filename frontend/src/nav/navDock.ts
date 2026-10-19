/*
 * Where the side nav sits and how it is shown: floating as a pill, docked as a full-length rail or bar, or that rail
 * minimized to icons. Per browser, in localStorage; storage may throw (private window), then the defaults apply.
 */
import { useSyncExternalStore } from 'react'

/** `minimized` is the docked rail shrunk to its icons. */
export type NavDock = 'float' | 'docked' | 'minimized'
/** Left is the original placement; top and bottom make the nav a horizontal bar. */
export type NavPosition = 'left' | 'right' | 'top' | 'bottom'

export const NAV_DOCK_KEY = 'baihe.navDock'
export const NAV_POSITION_KEY = 'baihe.navPosition'

export const NAV_POSITION_OPTIONS: { value: NavPosition; label: string }[] = [
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
  { value: 'top', label: 'Top' },
  { value: 'bottom', label: 'Bottom' },
]

export const parseNavDock = (raw: unknown): NavDock => (raw === 'docked' || raw === 'minimized' ? raw : 'float')
export const parseNavPosition = (raw: unknown): NavPosition => (raw === 'right' || raw === 'top' || raw === 'bottom' ? raw : 'left')

/** Below this a browser with no saved choice keeps the floating pill; the labelled rail needs the room. */
export const NAV_DOCK_DEFAULT_MIN_WIDTH = 1100

/** What a browser that never chose sees: the labelled rail on a wide screen, so a newcomer reads where things are. */
export const defaultNavDock = (width: number): NavDock => (width >= NAV_DOCK_DEFAULT_MIN_WIDTH ? 'docked' : 'float')

function makeStore<T extends string>(key: string, parse: (raw: unknown) => T, unsaved: () => T = () => parse(null)) {
  const fallback = parse(null)
  let current: T | null = null
  const listeners = new Set<() => void>()

  function load(): T {
    try {
      const raw = window.localStorage.getItem(key)
      return raw === null ? unsaved() : parse(raw)
    } catch {
      return unsaved()
    }
  }

  function emit(next: T): void {
    if (next === current) return
    current = next
    listeners.forEach((l) => l())
  }

  function subscribe(listener: () => void): () => void {
    listeners.add(listener)
    // Another tab changed the saved choice.
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === key) emit(load())
    }
    window.addEventListener('storage', onStorage)
    return () => {
      listeners.delete(listener)
      window.removeEventListener('storage', onStorage)
    }
  }

  return {
    set(next: T): void {
      try {
        window.localStorage.setItem(key, next)
      } catch {
        // Not remembered; it still applies to this page.
      }
      emit(next)
    },
    use: (): T => useSyncExternalStore(subscribe, () => (current ??= load()), () => fallback),
  }
}

const dockStore = makeStore(NAV_DOCK_KEY, parseNavDock, () => defaultNavDock(window.innerWidth))
const positionStore = makeStore(NAV_POSITION_KEY, parseNavPosition)

export const setNavDock = dockStore.set
export const useNavDock = dockStore.use
export const setNavPosition = positionStore.set
export const useNavPosition = positionStore.use
