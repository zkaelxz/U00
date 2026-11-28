import type { GlossaryTermUpsert } from '../../../types/translateStage'
import type { LanguagePackEntry, TitleLanguagePack } from '../../../types/languagePacks'

export const PACK_LANGUAGE_NAMES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }

/** The English of an entry in the given style (the pack default when unset). */
export function entryRendering(entry: LanguagePackEntry, pack: TitleLanguagePack): string {
  if (typeof entry.en === 'string') return entry.en
  return entry.en[pack.style ?? ''] ?? entry.en[pack.styles.default] ?? Object.values(entry.en)[0] ?? ''
}

/** What the server stores: every pack that is on, with its style. */
export function choiceAfter(
  packs: TitleLanguagePack[],
  change: { id: string; enabled?: boolean; style?: string },
): Record<string, string | null> {
  const next: Record<string, string | null> = {}
  for (const p of packs) {
    const enabled = p.id === change.id && change.enabled !== undefined ? change.enabled : p.enabled
    if (!enabled) continue
    const style = p.id === change.id && change.style !== undefined ? change.style : p.style
    next[p.id] = style || null
  }
  return next
}

export function packsSummary(packs: TitleLanguagePack[]): string {
  const on = packs.filter((p) => p.enabled).length
  return on === 0 ? 'starter packs, all off' : `starter packs, ${on} on`
}

/** A glossary term for the title, copied from one pack entry in the pack's current style. */
export function termFromEntry(entry: LanguagePackEntry, pack: TitleLanguagePack): GlossaryTermUpsert {
  const notes = [entry.context, entry.note].filter(Boolean).join('. ')
  const honorific = entry.category === 'honorific'
  return {
    term_original: entry.source,
    term_translation: entryRendering(entry, pack),
    notes,
    category: honorific ? 'honorific' : 'other',
    policy: honorific ? 'contextual' : 'translate_meaning',
  }
}

/** Entries shown at once; the VTuber name packs hold thousands, so the rest are found by search. */
export const SHOWN_ENTRIES = 100

/** The entries whose source, English or note contain `query` (all when empty), at most SHOWN_ENTRIES. */
export function shownEntries(entries: LanguagePackEntry[], query: string): { shown: LanguagePackEntry[]; matching: number } {
  const q = query.trim().toLowerCase()
  const matching = q
    ? entries.filter((e) => [e.source, typeof e.en === 'string' ? e.en : Object.values(e.en).join(' '), e.note]
        .some((text) => text.toLowerCase().includes(q)))
    : entries
  return { shown: matching.slice(0, SHOWN_ENTRIES), matching: matching.length }
}
