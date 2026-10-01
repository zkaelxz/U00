// Pure open-state storage helpers for Section (kept out of the component file for fast refresh).
const SECTION_KEY_PREFIX = 'baihe.section.'

export type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

export function sectionStorageKey(key: string): string {
  return SECTION_KEY_PREFIX + key
}

/** Remembered open state for `key`, or `fallback` if absent/unreadable. */
export function readSectionOpen(storage: StorageLike | null, key: string, fallback: boolean): boolean {
  if (!storage) return fallback
  try {
    const raw = storage.getItem(sectionStorageKey(key))
    if (raw === '1') return true
    if (raw === '0') return false
  } catch {
    // storage unavailable: fall through
  }
  return fallback
}

/** Remember open state; returns false (and does nothing else) if storage throws. */
export function writeSectionOpen(storage: StorageLike | null, key: string, open: boolean): boolean {
  if (!storage) return false
  try {
    storage.setItem(sectionStorageKey(key), open ? '1' : '0')
    return true
  } catch {
    return false
  }
}
