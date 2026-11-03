// Hand-written mirrors of api/schemas/review.py (Restructure*, Resegment*,
// RestoreVersion*). Every write sends `expected_line_ids`: the drama's line
// ids, in order, as the client last saw them; any difference is a 409.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

import type { ReviewLine } from './review'

export type RestructureAddLine = Schemas['RestructureAddLine']

export type RestructureSplit = Schemas['RestructureSplit']

export interface RestructureResult {
  // The drama's line ids after the change, in order.
  line_ids: number[]
  // The lines the change created or kept (split: both pieces; merge: the head; add: the new line).
  lines: ReviewLine[]
  // The snapshot taken just before this change and a fingerprint of the lines it left
  // (see UndoHandle); absent from an older server.
  history_id?: number | null
  lines_fingerprint?: string | null
}

interface ResegmentChange {
  line_id: number | null
  idx: number
  zh: string
  pieces: string[]
}

export interface ResegmentPreview {
  drama_id: number
  source_line_ids: number[]
  line_count_before: number
  line_count_after: number
  changed: ResegmentChange[]
  translated: number
  flagged: number
  notes: number
  needs_confirm: boolean
  // Why nothing would change; missing (an older server) or empty otherwise.
  reason?: string
}

// The rules path; the AI path is ResegmentLlmPreviewStart then apply.
export type ResegmentStart = Schemas['ResegmentStart']

// Parity R47: an LLM re-segmentation worked out as a preview (writes no lines).
// Blank fields are left out: the drama's engine and the engine's model apply.
export type ResegmentLlmPreviewStart = Schemas['ResegmentLlmPreviewStart']

// The rules preview's shape plus the engine that made it. There is no cost
// figure: the LLM usage is logged server-side with the drama's usage.
export interface ResegmentLlmPreview extends ResegmentPreview {
  engine: string
}

export type ResegmentStarted = Schemas['ResegmentStarted']

export type RestoreVersionResult = Schemas['RestoreVersionResult']

// POST /api/restructure/dramas/{id}/resplit: the finished summary (estimated
// timing) or, with align_to_audio, the started job.
export type ResplitStart = Schemas['ResplitStart']

export type ResplitSensitivity = 'more' | 'sentence'

export type ResplitResult = Schemas['ResplitResult']

export interface ReassignResult {
  changed: number
  kept_manual: number
}
