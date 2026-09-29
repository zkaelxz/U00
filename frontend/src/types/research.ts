// Mirrors api/metadata_research_schemas.py (roadmap Step 37: grounded research).

export type ResearchMode = 'quick' | 'deep' | 'verify'
export type ResearchChoice = 'keep' | 'replace' | 'save_both'

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
  free_remaining: number
  paid_price_per_search_usd: number
  free_tier_key: boolean
  key_configured: boolean
  monthly_cap_usd: number
  month_spend_usd: number
  models: string[]
  modes: string[]
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
  cost_usd: number
  budget: ResearchBudget
}

export interface ResearchRequest {
  mode: ResearchMode
  model?: string
  allow_paid?: boolean
}

export interface ResearchApplied {
  drama_id: number
  replaced: string[]
  saved_alternates: string[]
  kept: string[]
}
