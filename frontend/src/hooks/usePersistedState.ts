import { useCallback, useState } from 'react'

import type { StorageLike } from '../components/sectionStorage'

// Per-viewer conveniences (react-ui-guidelines rule 12) kept in localStorage as
// JSON under "baihe.pref.<key>". Storage may be missing or throw (private
// window, blocked site data); every access is wrapped and falls back.
export const PREF_KEY_PREFIX = 'baihe.pref.'

export function browserStorage(): StorageLike | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/** The remembered value, or `fallback` when absent, unreadable or a different type. */
export function readPref<T>(storage: StorageLike | null, key: string, fallback: T): T {
  if (!storage) return fallback
  try {
    const raw = storage.getItem(PREF_KEY_PREFIX + key)
    if (raw === null) return fallback
    const value = JSON.parse(raw) as unknown
    return typeof value === typeof fallback && value !== null ? (value as T) : fallback
  } catch {
    return fallback
  }
}

/** Remember a value; false when storage is unavailable. */
export function writePref<T>(storage: StorageLike | null, key: string, value: T): boolean {
  if (!storage) return false
  try {
    storage.setItem(PREF_KEY_PREFIX + key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function usePersistedState<T>(key: string, fallback: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => readPref(browserStorage(), key, fallback))
  const set = useCallback(
    (next: T) => {
      setValue(next)
      writePref(browserStorage(), key, next)
    },
    [key],
  )
  return [value, set]
}
