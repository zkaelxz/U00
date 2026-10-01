// Benchmark Lab (Step 38; api/routers/benchmark_routes.py, api/benchmark_schemas.py):
// golden sets, persistent per-run results and the Model Arena. Reads and the
// estimate need admin.diagnostics; adding, importing and deleting cases and
// starting a run are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403
// marks the tab remote). No key, path or file name is ever returned.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/benchmark'

export type BenchmarkStage = 'translation' | 'transcription' | 'ocr'
export type BenchmarkTier = 'public' | 'application' | 'regression'
export type SourceLanguage = 'zh' | 'ja' | 'ko'
export type ImportFormat = 'jsonl' | 'tsv'

export interface BenchmarkEngineOption {
  name: string
  label: string
  free: boolean
  models: string[] | null
  model_labels?: Record<string, string>
  key_configured: boolean
}

export interface BenchmarkOptions {
  stages: BenchmarkStage[]
  tiers: BenchmarkTier[]
  source_languages: SourceLanguage[]
  translation_engines: BenchmarkEngineOption[]
  whisper_sizes: string[]
  ocr_backends: string[]
  pass_threshold: number
  max_configs: number
}

export interface BenchmarkCase {
  id: number
  label: string
  stage: string | null
  tier: string
  set_name: string
  source_language: string
  source_text: string | null
  reference_text: string | null
  has_reference: boolean
  has_input_file: boolean
  origin_drama_id: number | null
  origin_line_id: number | null
  created_at: string | null
}

export interface BenchmarkSet {
  stage: string | null
  tier: string
  set_name: string
  case_count: number
  with_reference: number
}

export interface BenchmarkCaseCreate {
  label: string
  source_text: string
  reference_text?: string
  source_language: SourceLanguage
  tier: BenchmarkTier
  set_name?: string
}

export interface BenchmarkImportRequest {
  set_name: string
  text: string
  format: ImportFormat
  tier: BenchmarkTier
  source_language: SourceLanguage
}

export interface BenchmarkImportResult {
  set_name: string
  tier: string
  added: number
  skipped: number
}

export interface BenchmarkConfig {
  engine: string
  model?: string
}

export interface BenchmarkRunRequest {
  stage: BenchmarkStage
  configs: BenchmarkConfig[]
  tier?: BenchmarkTier
  set_name?: string
  case_ids?: number[]
  label?: string
  prompt_version?: string
}

export interface BenchmarkConfigEstimate {
  engine: string
  model: string | null
  estimated_cost_usd: number
  cap_applies: boolean
}

export interface BenchmarkEstimate {
  stage: string
  case_count: number
  configs: BenchmarkConfigEstimate[]
  estimated_cost_usd: number
  monthly_cap_usd: number
  month_spend_usd: number
  remaining_usd: number | null
  monthly_refusal: string | null
  estimate_above_cap: boolean
}

export interface BenchmarkRunStarted {
  job_id: string
  session_ids: number[]
  arena_group: string | null
  estimated_cost_usd: number
}

export interface BenchmarkRun {
  id: number
  label: string | null
  stage: string | null
  engine: string | null
  model: string | null
  prompt_version: string | null
  arena_group: string | null
  status: string | null
  case_count: number
  scored_count: number
  passed_count: number
  error_count: number
  aggregate_score: number | null
  avg_latency_seconds: number | null
  total_cost_usd: number | null
  peak_vram_mb: number | null
  note: string | null
  created_at: string | null
  finished_at: string | null
  context_settings: Record<string, unknown>
  case_filter: Record<string, unknown>
  delta_vs_first: number | null
}

export interface BenchmarkResult {
  case_id: number | null
  case_label: string
  output_text: string
  score: number | null
  metric: string | null
  /** 'jiwer' or 'builtin' for CER/WER; null on older results (built-in). */
  scorer?: string | null
  passed: boolean | null
  duration_seconds: number | null
  cost_usd: number
  error: string | null
}

export interface BenchmarkRunDetail {
  run: BenchmarkRun
  results: BenchmarkResult[]
}

export interface BenchmarkArenaRow {
  case_id: number | null
  label: string
  source_text: string | null
  reference_text: string | null
  tier: string | null
  results: (BenchmarkResult | null)[]
}

export interface BenchmarkArena {
  runs: BenchmarkRun[]
  rows: BenchmarkArenaRow[]
}

/** The query string for GET /cases: only the filters that are set. */
export function casesQuery(filters: { stage?: string | null; tier?: string | null; set_name?: string | null } = {}): string {
  const params = new URLSearchParams()
  if (filters.stage) params.set('stage', filters.stage)
  if (filters.tier) params.set('tier', filters.tier)
  // An empty set name is a real set ("no set"); the server treats it as "any", so it is left out.
  if (filters.set_name) params.set('set_name', filters.set_name)
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const getBenchmarkOptions = (f?: Fetch) => getJson<BenchmarkOptions>(`${BASE}/options`, f)

export const getBenchmarkSets = (f?: Fetch) => getJson<{ sets: BenchmarkSet[] }>(`${BASE}/sets`, f)

export const getBenchmarkCases = (filters: Parameters<typeof casesQuery>[0] = {}, f?: Fetch) =>
  getJson<{ cases: BenchmarkCase[] }>(`${BASE}/cases${casesQuery(filters)}`, f)

export const createBenchmarkCase = (body: BenchmarkCaseCreate, f?: Fetch) =>
  postJson<BenchmarkCase>(`${BASE}/cases`, body, pcOnlyFetch(f))

export const deleteBenchmarkCase = (id: number, f?: Fetch) =>
  postJson<{ deleted: boolean; id: number }>(`${BASE}/cases/${id}/delete`, { confirm: true }, pcOnlyFetch(f))

// "Add as regression test" from a Review line: keeps its source and current
// (hand-fixed) English as a regression case. Adding the same line again updates it.
export const addRegressionCase = (dramaId: number, lineId: number, f?: Fetch) =>
  postJson<{ case: BenchmarkCase; replaced: boolean }>(
    `${BASE}/dramas/${dramaId}/lines/${lineId}/regression`, {}, pcOnlyFetch(f))

// Reads pasted text only; nothing is downloaded.
export const importGoldenSet = (body: BenchmarkImportRequest, f?: Fetch) =>
  postJson<BenchmarkImportResult>(`${BASE}/import`, body, pcOnlyFetch(f))

// Spends nothing: what a run would cost and whether the monthly cap allows it.
export const estimateBenchmark = (body: BenchmarkRunRequest, f?: Fetch) =>
  postJson<BenchmarkEstimate>(`${BASE}/estimate`, body, f)

// Starts the background job "benchmark_lab" (poll it with useJob). The page shows the estimate first.
export const startBenchmarkRun = (body: BenchmarkRunRequest, f?: Fetch) =>
  postJson<BenchmarkRunStarted>(`${BASE}/runs`, { ...body, confirm: true }, pcOnlyFetch(f))

export function listBenchmarkRuns(opts: { stage?: string | null; limit?: number } = {}, f?: Fetch) {
  const params = new URLSearchParams()
  if (opts.stage) params.set('stage', opts.stage)
  if (opts.limit) params.set('limit', String(Math.max(1, Math.min(200, Math.round(opts.limit)))))
  const qs = params.toString()
  return getJson<{ runs: BenchmarkRun[] }>(`${BASE}/runs${qs ? `?${qs}` : ''}`, f)
}

export const getBenchmarkRun = (id: number, f?: Fetch) => getJson<BenchmarkRunDetail>(`${BASE}/runs/${id}`, f)

export function getBenchmarkArena(runIds: number[], f?: Fetch) {
  const params = new URLSearchParams()
  runIds.forEach((id) => params.append('run_ids', String(id)))
  return getJson<BenchmarkArena>(`${BASE}/arena?${params}`, f)
}
