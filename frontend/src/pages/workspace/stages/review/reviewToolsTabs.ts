import { readPref, writePref } from '../../../../hooks/usePersistedState'
import { sectionStorageKey, writeSectionOpen, type StorageLike } from '../../../../components/sectionStorage'

// The Review tools panel under the line list: one tab level. A deep link names a
// tab ("#/drama/3/review?tools=coverage").
export type ReviewToolsTab = 'check' | 'fix' | 'coverage' | 'restructure' | 'history' | 'extras'

export const REVIEW_TOOLS_TABS: { id: ReviewToolsTab; label: string }[] = [
  { id: 'check', label: 'Check' },
  { id: 'fix', label: 'Fix' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'restructure', label: 'Restructure' },
  { id: 'history', label: 'History' },
  { id: 'extras', label: 'Extras' },
]

export const isReviewToolsTab = (v: unknown): v is ReviewToolsTab => REVIEW_TOOLS_TABS.some((t) => t.id === v)

// The card that opens the first time a tab is shown; the rest start closed. A
// card that only holds a button row is not a fold, so it is never listed here. History has none:
// Records alone (notes, versions, line history) is taller than the screen, and the tab should fit one.
export const FIRST_CARD: Record<ReviewToolsTab, string | null> = {
  check: 'review.consistency',
  fix: 'review.flags',
  coverage: 'review.sourceChanged',
  restructure: 'review.structure',
  history: null,
  extras: 'review.aiExtras.sensevoice',
}

const tabPrefKey = (dramaId: number) => `review.tools.tab.${dramaId}`
const seededPrefKey = (tab: ReviewToolsTab) => `review.tools.seeded.${tab}`

export function readLastTab(storage: StorageLike | null, dramaId: number): ReviewToolsTab {
  const raw = readPref<string>(storage, tabPrefKey(dramaId), 'check')
  return isReviewToolsTab(raw) ? raw : 'check'
}

export function writeLastTab(storage: StorageLike | null, dramaId: number, tab: ReviewToolsTab): void {
  writePref(storage, tabPrefKey(dramaId), tab)
}

// Opens the tab's first card once per browser, before its sections mount, so
// the viewer's own later open/close choice is what Section remembers. A card
// the viewer already set either way is left alone.
export function seedFirstCard(storage: StorageLike | null, tab: ReviewToolsTab): void {
  const card = FIRST_CARD[tab]
  if (!storage || !card || readPref(storage, seededPrefKey(tab), false)) return
  try {
    if (storage.getItem(sectionStorageKey(card)) === null) writeSectionOpen(storage, card, true)
  } catch {
    return
  }
  writePref(storage, seededPrefKey(tab), true)
}
