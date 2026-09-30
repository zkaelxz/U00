// Mirrors api/metadata_research_schemas.py (roadmap Step 37: grounded research).

export type ResearchMode = 'quick' | 'deep' | 'verify'
export type ResearchChoice = 'keep' | 'replace' | 'save_both' | 'confirm'

export interface ResearchSource {
  title?: string | null
  url: string
}

export interface ResearchField {
  field: string
  value: string
  current?: string | null
  status: 'new' | 'same' | 'conflict'
  sources: ResearchSource[]
  confidence?: number | null
}

export interface ResearchBudget {
  free_daily_limit: number
  used_today: number
  free_monthly_limit: number
  used_this_month: number
  free_remaining: number
  paid_price_per_search_usd: number
  // Below this many free searches left, a lookup counts as paid.
  free_lookup_min: number
  free_tier_key: boolean
  key_configured: boolean
  monthly_cap_usd: number
  month_spend_usd: number
  models: string[]
  modes: string[]
  // Token cost of one lookup (before any paid search fee), by mode then model.
  estimates_usd: Record<string, Record<string, number>>
}

export interface ResearchResult {
  drama_id: number
  research_id: string
  cached: boolean
  mode: string
  model: string
  retrieved_at: string
  fields: ResearchField[]
  sources: ResearchSource[]
  related: { title: string; relation: string }[]
  search_queries: string[]
  cost_usd: number
  budget: ResearchBudget
}

export interface ResearchRequest {
  mode: ResearchMode
  model?: string
  allow_paid?: boolean
  refresh?: boolean
}

// One stored per-field evidence row. The API also returns source URLs; the
// note shows source titles only.
export interface ProvenanceRow {
  id: number
  field: string
  value: string
  source?: string | null
  source_url?: string | null
  sources: ResearchSource[]
  retrieved_at?: string | null
  confidence?: number | null
  last_verified?: string | null
  // 'applied' (replaced), 'alternate' (saved beside) or 'verified' (confirmed).
  status: string
}

export interface ProvenanceList {
  drama_id: number
  fields: ProvenanceRow[]
}

export interface ResearchApplied {
  drama_id: number
  replaced: string[]
  saved_alternates: string[]
  confirmed: string[]
  kept: string[]
}
