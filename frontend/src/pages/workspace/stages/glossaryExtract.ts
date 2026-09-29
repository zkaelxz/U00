// Pure helpers for the glossary extraction panels (From novel, From lines)
// and the review-before-translating step (unit-tested in
// glossaryExtract.test.ts). Proposals, selections and edits are keyed by
// term text, never by position.

import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import type { GlossaryProposalEdit } from '../../../types/glossaryHelpers'
import { novelGlossaryProgressText } from './autotuneGlossary'

export type GlossarySource = 'novel' | 'lines'

export interface SourceText {
  title: string
  intro: (engine: string) => string
  testId: string
  storageKey: string
}

export const SOURCE_TEXT: Record<GlossarySource, SourceText> = {
  novel: {
    title: 'From novel',
    intro: (engine) =>
      `Proposes names and terms from the attached novel using this drama's translation engine (${engine}). Nothing is added until you choose.`,
    testId: 'novel-glossary',
    storageKey: 'translate.glossary.novel',
  },
  lines: {
    title: 'From lines',
    intro: (engine) =>
      `Proposes names and terms from this drama's source lines using its translation engine (${engine}), in one request. Nothing is added until you choose.`,
    testId: 'lines-glossary',
    storageKey: 'translate.glossary.lines',
  },
}

// The lines run is one LLM call, so it has no meaningful percentage.
export function linesGlossaryProgressText(status: string): string {
  return status === 'queued' ? 'Waiting to start…' : 'Scanning the lines…'
}

export const extractionProgressText = (source: GlossarySource, status: string, progress: number | null) =>
  source === 'novel' ? novelGlossaryProgressText(status, progress) : linesGlossaryProgressText(status)

// Review before translating uses the novel when one is attached, else the
// source lines (Streamlit's "novel text, or else the lines" rule).
export const reviewSource = (hasNovel: boolean): GlossarySource => (hasNovel ? 'novel' : 'lines')

// --- Edits --------------------------------------------------------------

export interface ProposalValues {
  translation: string
  category: string | null
  policy: string | null
}

export type Edits = Record<string, ProposalValues>

export function proposalValues(p: NovelGlossaryProposal, edits: Edits): ProposalValues {
  return edits[p.term] ?? { translation: p.suggested_translation, category: p.category, policy: p.policy }
}

// Sets one field of a term's edit; a term whose values match the proposal
// again drops out of the edits.
export function editProposal<K extends keyof ProposalValues>(
  edits: Edits,
  p: NovelGlossaryProposal,
  field: K,
  value: ProposalValues[K],
): Edits {
  const next: ProposalValues = { ...proposalValues(p, edits), [field]: value }
  const rest = { ...edits }
  delete rest[p.term]
  const same =
    next.translation === p.suggested_translation && next.category === p.category && next.policy === p.policy
  return same ? rest : { ...rest, [p.term]: next }
}

// Overrides for the apply body: only chosen terms, only changed fields,
// translation trimmed. undefined when nothing was edited, so an unedited
// apply sends the same body as before.
export function buildOverrides(
  proposals: NovelGlossaryProposal[],
  chosen: string[],
  edits: Edits,
): Record<string, GlossaryProposalEdit> | undefined {
  const pick = new Set(chosen)
  const out: Record<string, GlossaryProposalEdit> = {}
  for (const p of proposals) {
    const e = edits[p.term]
    if (!e || !pick.has(p.term)) continue
    const o: GlossaryProposalEdit = {}
    const t = e.translation.trim()
    if (t !== p.suggested_translation) o.translation = t
    if (e.category !== p.category) o.category = e.category
    if (e.policy !== p.policy) o.policy = e.policy
    if (Object.keys(o).length) out[p.term] = o
  }
  return Object.keys(out).length ? out : undefined
}

// Chosen terms that would be added with no translation (the server
// reports them as "unknown"); the Add button waits until they're filled in.
export function missingTranslations(proposals: NovelGlossaryProposal[], chosen: string[], edits: Edits): string[] {
  const pick = new Set(chosen)
  return proposals.filter((p) => pick.has(p.term) && !proposalValues(p, edits).translation.trim()).map((p) => p.term)
}

export const missingTranslationText = (terms: string[]) =>
  `Add a translation for ${terms.length === 1 ? terms[0] : `${terms.length} terms`} first.`

// --- Review before translating (X28) ------------------------------------

export const startTranslationLabel = (adding: number) =>
  adding ? `Add ${adding} term${adding === 1 ? '' : 's'} and start translation` : 'Start translation'
