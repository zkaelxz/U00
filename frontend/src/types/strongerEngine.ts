// Step 99 "Try with a stronger engine" in Review (api/stronger_engine_schemas.py).
// Engine names, reasons and costs only; never a key, URL or path.

/** Why the stronger engine is suggested for a line. */
export type StrongerReason = 'qc_flag' | 'glossary_conflict' | 'ambiguous_term'

export interface StrongerLineSuggestion {
  line_id: number
  reasons: string[]
  estimate_usd: number
}

export interface StrongerEngineSuggestions {
  drama_id: number
  /** The engine Settings picked for "Stronger translation for hard lines". */
  engine: string
  /** The engine the drama already translates with. */
  current_engine: string
  /** False (and no lines) when both are the same engine. */
  available: boolean
  /** Reason key -> plain label ("Flagged in review"). */
  reason_labels: Record<string, string>
  lines: StrongerLineSuggestion[]
}

/** One try: nothing is written; the client applies text if the user chooses. */
export interface StrongerLineResult {
  drama_id: number
  line_id: number
  engine: string
  model: string | null
  text: string
  /** The English the engine saw; a stale apply is refused when the line changed. */
  based_on_en: string
  cost_usd: number
}
