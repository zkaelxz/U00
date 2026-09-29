import { DEFAULT_STAGE } from '../../router'

// Pipeline stages of the Workspace. Streamlit's Transcript and Diarize tabs
// live inside 'source' here. To add a stage: an id here, a label, and one
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
  done: 'done',
  current: 'next step',
  pending: 'not done yet',
  optional: 'optional',
  blocked: 'needs lines first',
}

export function stageStates(stages: { key: string; state: string }[] | undefined): Partial<Record<StageId, string>> {
  const out: Partial<Record<StageId, string>> = {}
  for (const s of stages ?? []) if (isStageId(s.key) && s.state in STAGE_STATE_WORDS) out[s.key] = s.state
  return out
}
