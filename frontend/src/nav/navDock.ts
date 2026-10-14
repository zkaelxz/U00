/*
 * Whether the side nav floats over the page as a pill, is docked as a full-height rail, or is that rail minimized to icons.
 * Per browser, in localStorage; storage may throw (private window), then it stays floating.
 */
import { useSyncExternalStore } from 'react'

/** `minimized` is the docked rail shrunk to its icons. */
export type NavDock = 'float' | 'docked' | 'minimized'

export const NAV_DOCK_KEY = 'baihe.navDock'

function load(): NavDock {
  try {
    const saved = window.localStorage.getItem(NAV_DOCK_KEY)
    return saved === 'docked' || saved === 'minimized' ? saved : 'float'
  } catch {
    return 'float'
  }
}

let current: NavDock | null = null
const listeners = new Set<() => void>()

function emit(next: NavDock): void {
  if (next === current) return
  current = next
  listeners.forEach((l) => l())
}

export function setNavDock(next: NavDock): void {
  try {
    window.localStorage.setItem(NAV_DOCK_KEY, next)
  } catch {
    // Not remembered; it still applies to this page.
  }
  emit(next)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === NAV_DOCK_KEY) emit(load())
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

export function useNavDock(): NavDock {
  return useSyncExternalStore(subscribe, () => (current ??= load()), () => 'float')
}
