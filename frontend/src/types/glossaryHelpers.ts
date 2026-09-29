// Mirrors api/schemas.py: LinesGlossaryRunResult, GlossaryProposalEdit and
// GlossaryProposalsApplyRequest (glossary_routes.py, parity X10/X28). The
// lines extraction's status and apply result reuse the NovelGlossary* types.
import type { NovelGlossaryApplyRequest } from './autotuneGlossary'

export interface LinesGlossaryRunResult {
  job_id: string
  engine: string
  line_count: number
}

// A field left out keeps the proposal's value; null category/policy = none.
export interface GlossaryProposalEdit {
  translation?: string
  category?: string | null
  policy?: string | null
}

// Edits are keyed by term text, never by position.
export interface GlossaryProposalsApplyRequest extends NovelGlossaryApplyRequest {
  overrides?: Record<string, GlossaryProposalEdit>
}
