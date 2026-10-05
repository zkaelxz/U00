import { DEFAULT_STAGE } from '../../router'

// Pipeline stages of the Workspace. Transcript and Diarize live inside
// 'source'. To add a stage: an id here, a label, and one
// line in stageRegistry.ts.
export const STAGE_IDS = ['source', 'translate', 'review', 'dub', 'export'] as const
export type StageId = (typeof STAGE_IDS)[number]

export const STAGE_LABELS: Record<StageId, string> = {
  source: 'Source',
  translate: 'Translate',
  review: 'Review',
  dub: 'Dub',
  export: 'Export',
}

export function isStageId(s: string): s is StageId {
  return (STAGE_IDS as readonly string[]).includes(s)
}

// An unknown stage in the URL falls back to the default stage.
export function parseStage(s: string): StageId {
  return isStageId(s) ? s : (DEFAULT_STAGE as StageId)
}

// P17: with no stage in the URL, open the stage the progress API reports;
// until it answers, null (wait); if it failed or names no known stage, the default.
export function startStage(
  urlStage: string | null,
  progress: { stage: string } | null,
  progressFailed: boolean,
): StageId | null {
  if (urlStage !== null) return parseStage(urlStage)
  if (progress) return parseStage(progress.stage)
  return progressFailed ? parseStage(DEFAULT_STAGE) : null
}

// P16: plain words for a stage's state in the stepper (the link's title,
// which is also its accessible description). Unknown states read as nothing.
export const STAGE_STATE_WORDS: Record<string, string> = {
  done: 'Done',
  current: 'Next step',
  pending: 'Not done yet',
  optional: 'Optional',
  blocked: 'Needs lines first',
}

export function stageStates(stages: { key: string; state: string }[] | undefined): Partial<Record<StageId, string>> {
  const out: Partial<Record<StageId, string>> = {}
  for (const s of stages ?? []) if (isStageId(s.key) && s.state in STAGE_STATE_WORDS) out[s.key] = s.state
  return out
}

// §3.3: the count a stepper tab shows next to its label, where one exists
// ("Translate · 32 left", "Review · 12 flagged"); null for none.
export function stageCount(
  stage: StageId,
  progress: { line_count: number; untranslated_count: number; flagged_count: number } | null,
): string | null {
  if (!progress || progress.line_count <= 0) return null
  if (stage === 'translate' && progress.untranslated_count > 0) return `${progress.untranslated_count} left`
  if (stage === 'review' && progress.flagged_count > 0) return `${progress.flagged_count} flagged`
  return null
}

// The one "Next" step offered once the open stage is done: the following stage
// that isn't blocked, worded from the progress counts only ("Translate 32 lines",
// "Review 12 flagged"). null when the open stage isn't done or nothing follows.
export interface NextAction {
  stage: StageId
  label: string
}

export function nextAction(
  active: StageId | null,
  progress: { untranslated_count: number; flagged_count: number; stages: { key: string; state: string }[] } | null,
): NextAction | null {
  if (!active || !progress) return null
  const states = stageStates(progress.stages)
  if (states[active] !== 'done') return null
  const next = STAGE_IDS.slice(STAGE_IDS.indexOf(active) + 1).find((s) => states[s] !== 'blocked')
  if (!next) return null
  const n = progress.untranslated_count
  const f = progress.flagged_count
  if (next === 'translate' && n > 0) return { stage: next, label: `Translate ${n} line${n === 1 ? '' : 's'}` }
  if (next === 'review' && f > 0) return { stage: next, label: `Review ${f} flagged` }
  return { stage: next, label: STAGE_LABELS[next] }
}
