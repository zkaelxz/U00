// Pure helpers for the glossary extraction panels (From novel, From lines)
// and the review-before-translating step (unit-tested in
// glossaryExtract.test.ts). Proposals, selections and edits are keyed by
// term text, never by position.

import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import type { GlossaryProposalEdit, GlossaryProposalsApplyRequest } from '../../../types/glossaryHelpers'
import { novelGlossaryApplyErrorText, novelGlossaryProgressText } from './autotuneGlossary'

export type GlossarySource = 'novel' | 'lines'

interface SourceText {
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
function linesGlossaryProgressText(status: string): string {
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

// --- Run scoping --------------------------------------------------------
// Selection, edits and a pending confirm belong to one run's proposals.
// Any panel (or another tab) can start a new run; state kept for an older
// run must not carry over (new terms would show unchecked and old edits
// would become overrides), so it reads as the initial value again.

export interface RunScoped<T> {
  run: string | null
  value: T
}

export const scopedValue = <T>(state: RunScoped<T>, run: string | null, initial: T): T =>
  state.run === run ? state.value : initial

// The apply body for the run the user reviewed: its run_id (the server
// answers 409 if the held run is another one), the chosen terms, and only
// the edits that differ from the proposals shown.
export function applyRequest(
  chosen: string[],
  runId: string | null,
  overrides: Record<string, GlossaryProposalEdit> | undefined,
  overwrite = false,
): GlossaryProposalsApplyRequest {
  return {
    terms: chosen,
    ...(overwrite ? { overwrite_existing: true, confirm: true } : {}),
    ...(overrides ? { overrides } : {}),
    ...(runId ? { run_id: runId } : {}),
  }
}

export const PROPOSALS_CHANGED_TEXT = 'The proposals changed since you reviewed them — review again.'

// 409 on apply: the extraction was run again since these proposals were
// shown; 400: no finished run held (app restarted).
export function glossaryApplyErrorText(err: unknown): string | null {
  const e = err as { status?: number } | null
  return e?.status === 409 ? PROPOSALS_CHANGED_TEXT : novelGlossaryApplyErrorText(err)
}
