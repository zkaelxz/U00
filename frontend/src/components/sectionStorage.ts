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

const TAB_SEEN_PREFIX = 'baihe.tabfolds.'

export type SeedStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

/**
 * First visit to a tab: make the card defaults (first card open, the rest closed) win over any state
 * saved before this rule existed, so a tab whose cards were all left collapsed does not stay blank.
 * A tab where the person already has something open keeps their folds. Runs once per tab, and must run
 * before the tab's Sections mount because they read storage when they are created.
 */
export function seedTabFolds(storage: SeedStorage | null, scope: string, tab: string, cardKeys: readonly string[]): void {
  if (!storage) return
  try {
    const seen = TAB_SEEN_PREFIX + scope + '.' + tab
    if (storage.getItem(seen)) return
    const hasOpen = cardKeys.some((k) => storage.getItem(sectionStorageKey(k)) === '1')
    if (!hasOpen) cardKeys.forEach((k) => storage.removeItem(sectionStorageKey(k)))
    storage.setItem(seen, '1')
  } catch {
    // storage unavailable: the defaults apply anyway
  }
}

/** seedTabFolds for every tab of a page, on the browser's localStorage. */
export function seedPageTabFolds(scope: string, tabs: Record<string, readonly string[]>): void {
  let storage: SeedStorage | null = null
  try {
    storage = typeof window === 'undefined' ? null : window.localStorage
  } catch {
    // blocked site data
  }
  for (const [tab, keys] of Object.entries(tabs)) seedTabFolds(storage, scope, tab, keys)
}
