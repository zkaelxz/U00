import { readPref, writePref } from '../../hooks/usePersistedState'
import type { StorageLike } from '../../components/sectionStorage'
import type { DramaFocus } from '../../router'

/** The sub-tabs of the Translate stage. */
export type TranslateTab = 'translate' | 'glossary' | 'characters' | 'novel'

export const TRANSLATE_TABS: { id: TranslateTab; label: string }[] = [
  { id: 'translate', label: 'Translate' },
  { id: 'glossary', label: 'Glossary' },
  { id: 'characters', label: 'Characters' },
  { id: 'novel', label: 'Reference novel' },
]

export const isTranslateTab = (v: unknown): v is TranslateTab => TRANSLATE_TABS.some((t) => t.id === v)

/** The tab to show: the wanted one, else Translate. */
export const resolveTranslateTab = (wanted: string | null | undefined): TranslateTab =>
  TRANSLATE_TABS.find((t) => t.id === wanted)?.id ?? 'translate'

// A "?focus=glossary" link (Reader's Edit in Translate) points at a panel that now lives on its own tab.
export function tabForFocus(focus: DramaFocus | undefined): TranslateTab | null {
  return focus === 'glossary' || focus === 'characters' ? focus : null
}

// Per title, so opening another title never lands on a tab chosen for this one.
const prefKey = (dramaId: number) => `translate.tab.${dramaId}`

export function readTranslateTab(storage: StorageLike | null, dramaId: number): TranslateTab | null {
  const v = readPref(storage, prefKey(dramaId), '')
  return isTranslateTab(v) ? v : null
}

export const writeTranslateTab = (storage: StorageLike | null, dramaId: number, tab: TranslateTab) =>
  writePref(storage, prefKey(dramaId), tab)

export const TRANSLATE_TAB_PREFIX = 'translate'
