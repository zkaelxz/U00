// Model re-evaluation (Step 40b; api/routers/model_reeval_routes.py,
// api/model_reeval_schemas.py): the production model, the re-evaluation
// schedule, candidate models with their recorded decisions, the latest
// report and the decision history. Reads and the estimate need
// admin.diagnostics; every write is PC only and goes through pcOnlyFetch
// (X-Baihe-Local; a 403 marks the tab remote). "Run now" and "Promote" send
// confirm: true; the page only calls them after an estimate / a second press.
import type { BenchmarkEstimate } from './benchmark'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/models/reeval'

export type CandidateStatus = 'candidate' | 'promoted' | 'rejected'

export interface ModelDecision {
  id: number
  candidate_id: number
  decision: string
  reason: string
  scores: Record<string, unknown>
  decided_at: string | null
  // "Already evaluated on <date>, <decision>: <reason>" (written by the server).
  summary: string
}

export interface ModelCandidate {
  id: number
  capability: string
  engine: string
  model: string | null
  note: string | null
  status: CandidateStatus | string
  created_at: string | null
  last_decision: ModelDecision | null
}

export interface ProductionModel {
  engine: string
  model: string | null
  // 'promoted' only while Settings' default engine is still the promoted one's engine.
  source: 'settings' | 'promoted' | string
  promoted_at?: string | null
}

export interface ReevalSettings {
  schedule_enabled: boolean
  interval_days: number
  tier: string | null
  set_name: string | null
  // A scheduled run estimated above this is skipped (null: only the monthly cap applies).
  max_cost_usd: number | null
}

export interface ReevalRow {
  candidate: ModelCandidate
  run_id: number
  status: string | null
  aggregate_score: number | null
  quality_delta: number | null
  cost_delta_usd: number | null
  latency_delta_seconds: number | null
  vram_delta_mb: number | null
  total_cost_usd: number | null
  avg_latency_seconds: number | null
}

export interface ReevalProductionRun {
  id: number
  status: string | null
  aggregate_score: number | null
  total_cost_usd: number | null
  avg_latency_seconds: number | null
  peak_vram_mb: number | null
  engine: string | null
  model: string | null
}

export interface ReevalReport {
  production: ProductionModel
  production_run: ReevalProductionRun | null
  started_at: string | null
  scheduled: boolean
  arena_group: string | null
  // Why the last scheduled attempt was refused (cap used up, no cases...).
  error: string | null
  error_at?: string | null
  rows: ReevalRow[]
}

export interface ReevalOverview {
  capability: string
  production: ProductionModel
  settings: ReevalSettings
  next_due_at: string | null
  candidates: ModelCandidate[]
  report: ReevalReport
}

export interface ReevalSettingsRequest {
  schedule_enabled: boolean
  interval_days: number
  tier?: string | null
  set_name?: string | null
  max_cost_usd?: number | null
}

export interface CandidateAddRequest {
  engine: string
  model?: string
  note?: string
}

export interface CandidateAddResult {
  candidate: ModelCandidate
  already_registered: boolean
}

export interface PromoteResult {
  production: ProductionModel
  previous: ProductionModel
  default_engine_changed: boolean
  candidate: ModelCandidate
}

export interface ReevalRunStarted {
  job_id: string
  session_ids: number[]
  arena_group: string | null
  estimated_cost_usd: number
  candidate_ids: number[]
}

export const getReevalOverview = (f?: Fetch) => getJson<ReevalOverview>(BASE, f)

export const getReevalDecisions = (f?: Fetch) => getJson<{ decisions: ModelDecision[] }>(`${BASE}/decisions`, f)

// Spends nothing: what "Run now" would cost (production + every open candidate).
export const estimateReeval = (f?: Fetch) => postJson<BenchmarkEstimate>(`${BASE}/estimate`, undefined, f)

// The server replaces the whole schedule record (a left-out tier, set or limit is cleared), so every field is sent.
export const saveReevalSettings = (body: ReevalSettingsRequest, f?: Fetch) =>
  postJson<ReevalOverview>(`${BASE}/settings`, body, pcOnlyFetch(f))

export const addReevalCandidate = (body: CandidateAddRequest, f?: Fetch) =>
  postJson<CandidateAddResult>(`${BASE}/candidates`, body, pcOnlyFetch(f))

export const rejectReevalCandidate = (id: number, reason: string, f?: Fetch) =>
  postJson<{ candidate: ModelCandidate }>(`${BASE}/candidates/${id}/reject`, { reason }, pcOnlyFetch(f))

export const reopenReevalCandidate = (id: number, f?: Fetch) =>
  postJson<{ candidate: ModelCandidate }>(`${BASE}/candidates/${id}/reopen`, undefined, pcOnlyFetch(f))

// The only call that changes the production model (and Settings' default engine when the engine differs).
export const promoteReevalCandidate = (id: number, reason: string, f?: Fetch) =>
  postJson<PromoteResult>(`${BASE}/candidates/${id}/promote`, { confirm: true, reason }, pcOnlyFetch(f))

// Starts the "benchmark_lab" job (poll it with useJob); the page shows the estimate first.
export const startReevalRun = (f?: Fetch) => postJson<ReevalRunStarted>(`${BASE}/run`, { confirm: true }, pcOnlyFetch(f))
