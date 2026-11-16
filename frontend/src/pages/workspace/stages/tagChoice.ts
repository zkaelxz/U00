// What a chunk-and-tag choice means for the request, the estimate text and the spending confirmation.
import type { NarrationConfig, NarrationEstimate, NarrationRunRequest } from '../../../types/dub'
import { CHAPTER_RANGE_MESSAGE, chapterRange } from '../translateForm'

/** What a chunk-and-tag run is told: which engine, whether to tag at all, and which chapters. */
export interface TagChoice {
  engine: string // '' = the server's default engine
  model: string
  oneNarrator: boolean // one voice reads everything: no AI call, no cost
  from: string // '' = from the first chapter
  to: string // '' = to the last
}

export const EMPTY_TAG_CHOICE: TagChoice = { engine: '', model: '', oneNarrator: false, from: '', to: '' }

export function effectiveTagEngine(c: TagChoice, cfg: Pick<NarrationConfig, 'default_engine'>): string {
  return c.engine || cfg.default_engine
}

export function tagChoiceProblem(c: TagChoice): string | null {
  return chapterRange(c.from, c.to) === null ? CHAPTER_RANGE_MESSAGE : null
}

/** The request for a choice; call after tagChoiceProblem returned null. The engine is always named, so no other one can be picked for the person. */
export function tagRunBody(c: TagChoice, defaultEngine: string): NarrationRunRequest {
  return {
    engine: c.engine || defaultEngine,
    ...(c.model.trim() ? { model: c.model.trim() } : {}),
    ...(c.oneNarrator ? { one_narrator: true } : {}),
    ...(chapterRange(c.from, c.to) ?? {}),
  }
}

/** A paid run with no monthly cap set is only started once the person has accepted the estimate. */
export function needsCostConfirm(e: NarrationEstimate | null, noFigure = false): boolean {
  return !!e && e.has_key && e.cap_applies && !e.monthly_cap_set && (noFigure || e.estimated_usd > 0)
}

export function estimateText(e: NarrationEstimate): string {
  const scope = `${e.chunk_count.toLocaleString()} lines in ${e.chapters.toLocaleString()} chapter${e.chapters === 1 ? '' : 's'}`
  if (!e.has_key) return `${scope}. No key is set for this engine, so every line is marked Narrator and nothing is spent.`
  if (!e.cap_applies) return `${scope}. This engine is free, so nothing is spent.`
  return `${scope}. Estimated cost about $${e.estimated_usd.toFixed(2)}.`
}
