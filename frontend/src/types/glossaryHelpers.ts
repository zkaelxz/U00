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

// Edits are keyed by term text, never by position. run_id is the status's
// run_id the user reviewed (required by from-lines, optional on from-novel;
// the client always sends it); 409 if the held run is another one.
export interface GlossaryProposalsApplyRequest extends NovelGlossaryApplyRequest {
  overrides?: Record<string, GlossaryProposalEdit>
  run_id?: string
}
