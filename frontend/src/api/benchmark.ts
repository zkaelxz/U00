// Benchmark Lab (api/routers/benchmark_routes.py, api/benchmark_schemas.py):
// golden sets, persistent per-run results and the Model Arena. Reads and the
// estimate need admin.diagnostics; adding, importing and deleting cases and
// starting a run are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403
// marks the tab remote). No key, path or file name is ever returned.
import { apiUrl, getJson, postJson, postMultipart } from './client'
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

/** A transcription arm's per-title options; a field left out takes the app's default (BenchmarkOptions.transcribe_defaults). */
export interface BenchmarkTranscribeOptions {
  beam_size?: number
  min_silence_ms?: number
  vad_threshold?: number
  sensitivity_preset?: string
  hallucination_silence_sec?: number
  whisper_repeat_guard?: boolean
  difficult_audio?: boolean
  separate_vocals_first?: boolean
  initial_prompt?: string
  /** Timing settings only the Lab varies. */
  speech_pad_ms?: number
  refine_timing?: boolean
  split_max_seconds?: number
  split_max_chars?: number
}

/** A run's line timing over the cases that have reference times; only `cases` (0) when none do. */
export interface BenchmarkTimingSummary {
  cases: number
  start_median_ms?: number | null
  end_median_ms?: number | null
  /** Share of timed cases whose start and end are both within 0.3 s of the reference. */
  within?: number | null
  merged?: number | null
  split?: number | null
  missed?: number | null
  silence?: number | null
}

export interface BenchmarkCaseTiming {
  lines: number
  missed: boolean
  split: boolean
  merged: boolean
  start_error_s: number | null
  end_error_s: number | null
  silence: number | null
}

/** Arena only: whether a run's timing beat the first run's by more than the measured noise without a worse text score. */
export interface BenchmarkTimingVerdict {
  state: 'baseline' | 'repeat' | 'untimed' | 'no_margin' | 'no_gain' | 'cer_worse' | 'gain'
  text: string
  margin_within: number | null
  margin_score: number | null
  gain?: number | null
  score_change?: number | null
}

export interface BenchmarkBackendOption {
  name: string
  label: string
  /** What it still needs on this PC (a package or a model to download); null when it can run. */
  problem: string | null
}

export interface BenchmarkOptions {
  stages: BenchmarkStage[]
  tiers: BenchmarkTier[]
  source_languages: SourceLanguage[]
  translation_engines: BenchmarkEngineOption[]
  whisper_sizes: string[]
  // {model: {language: reason}} for models not recommended for a language.
  whisper_exclusions: Record<string, Record<string, string>>
  /** Recognisers besides Whisper (Qwen3-ASR, anime-whisper); they run only when already installed. */
  transcription_backends: BenchmarkBackendOption[]
  transcribe_defaults: Required<BenchmarkTranscribeOptions>
  /** Per recogniser ("whisper" included), the options it does not read. */
  transcribe_options_ignored: Record<string, string[]>
  ocr_backends: string[]
  pass_threshold: number
  chrf_pass_threshold: number
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
  title: string
  tags: string[]
  scene_order: number | null
  /** An excluded case is not run and drops out of comparisons; its results are kept. */
  excluded: boolean
  /** Goes up by one each time the reference is edited. */
  ref_version: number
  /** The case has a reference start and end, so a run scores its line timing. */
  has_ref_times?: boolean
  created_at: string | null
}

export interface BenchmarkSet {
  stage: string | null
  tier: string
  set_name: string
  case_count: number
  with_reference: number
  /** Cases left out of scoring. */
  excluded?: number
}

interface BenchmarkCaseCreate {
  label: string
  source_text: string
  reference_text?: string
  source_language: SourceLanguage
  tier: BenchmarkTier
  set_name?: string
}

interface BenchmarkImportRequest {
  set_name: string
  text: string
  format: ImportFormat
  tier: BenchmarkTier
  source_language: SourceLanguage
}

interface BenchmarkImportResult {
  set_name: string
  tier: string
  added: number
  skipped: number
}

export interface BenchmarkConfig {
  engine: string
  model?: string
  /** Transcription only, and only the fields changed from the app's defaults. */
  options?: BenchmarkTranscribeOptions
}

/** The engine that scores a translation run's outputs. allow_same_model must be true to judge with a tested model. */
export interface BenchmarkJudgeConfig {
  engine: string
  model?: string
  allow_same_model?: boolean
}

export interface BenchmarkRunRequest {
  stage: BenchmarkStage
  configs: BenchmarkConfig[]
  tier?: BenchmarkTier
  set_name?: string
  case_ids?: number[]
  label?: string
  prompt_version?: string
  judge?: BenchmarkJudgeConfig
}

/** One output's judge scores, each 0-1. */
export interface BenchmarkJudgeScore {
  accuracy: number
  tone: number
  naturalness: number
  overall: number
}

export interface BenchmarkJudgeSummary {
  engine: string | null
  model: string | null
  status: string | null
  same_as_tested: boolean
  cost_usd: number
  scored: number
  average: Partial<Record<keyof BenchmarkJudgeScore, number | null>>
  note: string | null
}

export interface BenchmarkJudgeEstimate {
  engine: string
  model: string | null
  estimated_cost_usd: number
  cap_applies: boolean
  same_as_tested: string[]
  warning: string | null
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
  /** Present when the request asked for a judge; estimated_cost_usd already includes it. */
  judge?: BenchmarkJudgeEstimate | null
}

export interface BenchmarkRunStarted {
  job_id: string
  session_ids: number[]
  arena_group: string | null
  estimated_cost_usd: number
}

/** Traditional vs Simplified in a Chinese transcript; null for other languages and stages. */
export interface ScriptCounts {
  traditional_lines: number
  simplified_lines: number
  mixed_lines: number
  neutral_lines: number
  traditional_chars: number
  simplified_chars: number
  neutral_chars: number
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
  /** Arena only: the run's own total over all its cases (aggregate_score covers only the compared ones). */
  stored_aggregate_score?: number | null
  judge?: BenchmarkJudgeSummary | null
  script_counts?: ScriptCounts | null
  timing?: BenchmarkTimingSummary
  timing_verdict?: BenchmarkTimingVerdict | null
}

export interface BenchmarkResult {
  case_id: number | null
  case_label: string
  output_text: string
  score: number | null
  metric: string | null
  /** 'jiwer' or 'builtin' for CER/WER, 'sacrebleu' or 'builtin' for translation; null on older results (built-in). */
  scorer?: string | null
  passed: boolean | null
  duration_seconds: number | null
  cost_usd: number
  error: string | null
  judge?: BenchmarkJudgeScore | null
  script_counts?: ScriptCounts | null
  ref_version?: number
  timing?: BenchmarkCaseTiming | null
}

interface BenchmarkRunDetail {
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

/** Cases an Arena comparison skipped, and why. */
export interface BenchmarkLeftOut {
  total: number
  removed: number
  excluded: number
  edited: number
  not_in_every_run: number
}

export interface BenchmarkArena {
  runs: BenchmarkRun[]
  rows: BenchmarkArenaRow[]
  /** Absent for a single run's results. */
  left_out?: BenchmarkLeftOut | null
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

export type BuildInclude = 'reviewed' | 'all'

export interface BuildSetRequest {
  drama_id: number
  set_name: string
  include: BuildInclude
  line_start?: number
  line_end?: number
  scene_count?: number
  lines_per_case: number
  dry_run: boolean
}

export interface BuildSetResult {
  set_name: string
  tier: string
  include: BuildInclude
  lines_in_range: number
  reviewed_lines: number
  lines_used: number
  case_count: number
  added: number
  skipped: number
  dry_run: boolean
}

// Cases from a title's lines: its reviewed (hand-edited or approved) translations are the references.
// dry_run only counts. PC only.
export const buildBenchmarkSet = (body: BuildSetRequest, f?: Fetch) =>
  postJson<BuildSetResult>(`${BASE}/sets/from-title`, body, pcOnlyFetch(f))

export interface TitleCorrections {
  drama_id: number
  raw_available: boolean
  lines_compared: number
  lines_corrected: number
  /** Share of the corrected text that differs from the raw transcript (0.1 = 10%); null with no corrections. */
  raw_to_edited_cer: number | null
}

export interface AddCorrectionsResult {
  set_name: string
  lines_compared: number
  lines_corrected: number
  unedited_skipped: number
  unclippable_skipped: number
  include_all?: boolean
  case_count: number
  /** Lines left for another add: one call adds at most 100. */
  remaining: number
  added: number
  skipped: number
  dry_run: boolean
}

export const getTitleCorrections = (dramaId: number, f?: Fetch) =>
  getJson<TitleCorrections>(`${BASE}/dramas/${dramaId}/corrections`, f)

// Transcription cases from a title's corrected lines (every spoken line with include_all), each with its audio clipped.
// dry_run only counts. PC only.
export const addTitleCorrections = (dramaId: number, body: { set_name: string; dry_run: boolean; include_all?: boolean }, f?: Fetch) =>
  postJson<AddCorrectionsResult>(`${BASE}/dramas/${dramaId}/corrections/add`, body, pcOnlyFetch(f))

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

// ---- the owner's clip set -------------------------------------------------

export interface ClipRowPayload {
  filename: string
  language: SourceLanguage
  transcript: string
  translation?: string
  title?: string
  tags?: string[]
}

export interface ClipNote {
  clip: string
  message: string
}

export interface ClipImportResult {
  set_name: string
  clips: number
  transcription_cases: number
  translation_cases: number
  case_ids: number[]
  warnings: ClipNote[]
}

// Reports what would refuse the import and kana/Hangul that contradicts a language label; saves nothing.
export const checkBenchmarkClips = (clips: ClipRowPayload[], f?: Fetch) =>
  postJson<{ problems: ClipNote[]; warnings: ClipNote[] }>(`${BASE}/clips/check`, { clips }, f)

// Uploads the audio with its rows. PC only. Paths and file names are never returned.
export function importBenchmarkClips(body: { set_name: string; clips: ClipRowPayload[]; files: File[] }, f?: Fetch) {
  const form = new FormData()
  form.set('set_name', body.set_name)
  form.set('clips', JSON.stringify(body.clips))
  body.files.forEach((file) => form.append('files', file, file.name))
  return postMultipart<ClipImportResult>(`${BASE}/clips/import`, form, pcOnlyFetch(f))
}

export const setCaseExcluded = (id: number, excluded: boolean, f?: Fetch) =>
  postJson<BenchmarkCase>(`${BASE}/cases/${id}/excluded`, { excluded }, pcOnlyFetch(f))

// Fixes a transcript or translation in place; the change is logged and the case's version goes up.
export const editCaseReference = (id: number, reference_text: string, f?: Fetch) =>
  postJson<BenchmarkCase>(`${BASE}/cases/${id}/reference`, { reference_text }, pcOnlyFetch(f))

export interface CaseEdit {
  field: string
  old_text: string | null
  new_text: string | null
  version: number
  edited_at: string | null
}

export const getCaseEdits = (id: number, f?: Fetch) => getJson<{ edits: CaseEdit[] }>(`${BASE}/cases/${id}/edits`, f)

// Removes a run and its results; its cases stay.
export const deleteBenchmarkRun = (id: number, f?: Fetch) =>
  postJson<{ deleted: boolean; id: number }>(`${BASE}/runs/${id}/delete`, { confirm: true }, pcOnlyFetch(f))

export type ExportFormat = 'csv' | 'json'

/** Download links: the setup, scores and each case's output for a run, or for runs compared on their shared cases. */
export const runExportUrl = (id: number, format: ExportFormat) => apiUrl(`${BASE}/runs/${id}/export?format=${format}`)

export function arenaExportUrl(runIds: number[], format: ExportFormat) {
  const params = new URLSearchParams()
  runIds.forEach((id) => params.append('run_ids', String(id)))
  params.set('format', format)
  return apiUrl(`${BASE}/arena/export?${params}`)
}
