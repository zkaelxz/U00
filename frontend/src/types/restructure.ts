// Hand-written mirrors of api/schemas.py (Restructure*, Resegment*,
// RestoreVersion*). Every write sends `expected_line_ids`: the drama's line
// ids, in order, as the client last saw them; any difference is a 409.
import type { ReviewLine } from './review'

export interface RestructureAddLine {
  expected_line_ids: number[]
  // null = insert at the start.
  after_line_id: number | null
  start: number
  end: number
  zh?: string
  en?: string
  speaker?: string | null
}

export interface RestructureSplit {
  expected_line_ids: number[]
  // Offsets count Unicode code points (Python str indices), not UTF-16 units.
  at_char: number
  expected_zh: string
  at_time?: number | null
  en_at_char?: number | null
}

export interface RestructureResult {
  // The drama's line ids after the change, in order.
  line_ids: number[]
  // The lines the change created or kept (split: both pieces; merge: the head; add: the new line).
  lines: ReviewLine[]
}

export interface ResegmentChange {
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
}

// The rules path; the AI path is ResegmentLlmPreviewStart then apply.
export interface ResegmentStart {
  expected_line_ids: number[]
  confirm: boolean
}

// Parity R47: an LLM re-segmentation worked out as a preview (writes no lines).
// Blank fields are left out: the drama's engine and the engine's model apply.
export interface ResegmentLlmPreviewStart {
  engine?: string
  model?: string
}

// The rules preview's shape plus the engine that made it. There is no cost
// figure: the LLM usage is logged server-side with the drama's usage.
export interface ResegmentLlmPreview extends ResegmentPreview {
  engine: string
}

export interface ResegmentStarted {
  job_id: string
  drama_id: number
}

export interface RestoreVersionResult {
  history_id: number
  line_ids: number[]
}
