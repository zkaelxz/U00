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
