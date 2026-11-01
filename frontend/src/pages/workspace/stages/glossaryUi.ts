// What the Glossary panel remembers for a title in this browser: the tab, the Suggest-terms source,
// the term whose spelling panel is open (and the English names typed there) and whether the
// all-titles name check was open. One record per title under "baihe.pref.glossary.ui.<id>"; a
// different version reads as nothing remembered, so a shape change bumps the version.
import { browserStorage, readPref, writePref } from '../../../hooks/usePersistedState'
import type { StorageLike } from '../../../components/sectionStorage'
import type { GlossarySource } from './glossaryExtract'
import { isGlossaryTab, type GlossaryTab } from './glossaryTabs'

const GLOSSARY_UI_VERSION = 1

interface GlossaryUi {
  tab: GlossaryTab | null
  source: GlossarySource | null
  spellingTermId: number | null
  // Term id -> the English name typed for "Suggest spellings"; kept so a paid request is not retyped.
  english: Record<string, string>
  nameReportOpen: boolean
}

export const EMPTY_GLOSSARY_UI: GlossaryUi = { tab: null, source: null, spellingTermId: null, english: {}, nameReportOpen: false }

const key = (dramaId: number) => `glossary.ui.${dramaId}`
const isSource = (v: unknown): v is GlossarySource => v === 'novel' || v === 'lines'

export function parseGlossaryUi(raw: unknown): GlossaryUi {
  if (typeof raw !== 'object' || raw === null || (raw as { v?: unknown }).v !== GLOSSARY_UI_VERSION) return EMPTY_GLOSSARY_UI
  const r = raw as Record<string, unknown>
  const english: Record<string, string> = {}
  if (typeof r.english === 'object' && r.english !== null && !Array.isArray(r.english)) {
    for (const [k, v] of Object.entries(r.english)) if (typeof v === 'string') english[k] = v
  }
  return {
    tab: isGlossaryTab(r.tab) ? r.tab : null,
    source: isSource(r.source) ? r.source : null,
    spellingTermId: typeof r.spellingTermId === 'number' ? r.spellingTermId : null,
    english,
    nameReportOpen: r.nameReportOpen === true,
  }
}

export const readGlossaryUi = (storage: StorageLike | null, dramaId: number): GlossaryUi =>
  parseGlossaryUi(readPref<unknown>(storage, key(dramaId), {}))

// Merges into what is stored (English names are added to, not replaced); returns the new record.
export function writeGlossaryUi(storage: StorageLike | null, dramaId: number, patch: Partial<GlossaryUi>): GlossaryUi {
  const cur = readGlossaryUi(storage, dramaId)
  const next: GlossaryUi = { ...cur, ...patch, english: { ...cur.english, ...(patch.english ?? {}) } }
  writePref(storage, key(dramaId), { v: GLOSSARY_UI_VERSION, ...next })
  return next
}

export const loadGlossaryUi = (dramaId: number) => readGlossaryUi(browserStorage(), dramaId)
export const saveGlossaryUi = (dramaId: number, patch: Partial<GlossaryUi>) => writeGlossaryUi(browserStorage(), dramaId, patch)
