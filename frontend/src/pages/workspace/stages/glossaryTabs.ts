// The Glossary panel's tabs: one level, shared Tabs. Panels stay mounted (hidden) so a run's
// state and an open editor survive a tab switch.
import type { TabInfo } from '../../../components/Tabs'

export type GlossaryTab = 'terms' | 'find' | 'names' | 'instructions' | 'retranslate'

export const GLOSSARY_TABS: TabInfo<GlossaryTab>[] = [
  { id: 'terms', label: 'Terms' },
  { id: 'find', label: 'Find terms' },
  { id: 'names', label: 'Names' },
  { id: 'instructions', label: 'Instructions' },
  { id: 'retranslate', label: 'Re-translate' },
]

export const isGlossaryTab = (v: unknown): v is GlossaryTab => GLOSSARY_TABS.some((t) => t.id === v)

export function instructionsSummary(text: string): string {
  const n = text.length
  return text.trim() ? `set, ${n} character${n === 1 ? '' : 's'}` : 'not set'
}

const NEEDS_SERIES = 'Add this title to a series first; the glossary belongs to the series.'

// Shown in place of a tab's content when it has nothing to act on; null when the tab has its own
// content. Find terms has no message: its bar names what each source still needs.
// termCount is null until the terms are read, so a message never flashes while loading.
export function emptyTabText(tab: GlossaryTab, ctx: { hasSeries: boolean; termCount: number | null }): string | null {
  if (tab === 'terms') return !ctx.hasSeries && ctx.termCount === 0 ? `No terms yet. ${NEEDS_SERIES}` : null
  if (tab === 'retranslate') return ctx.hasSeries ? null : NEEDS_SERIES
  return null
}
