// Benchmark Lab (api/routers/benchmark_routes.py, api/benchmark_schemas.py):
// golden sets, persistent per-run results and the Model Arena. Reads and the
// estimate need admin.diagnostics; adding, importing and deleting cases and
// starting a run are PC only and go through pcOnlyFetch (X-Baihe-Local; a 403
// marks the tab remote). No key, path or file name is ever returned.
import { apiUrl, getJson, postJson, postMultipart, queryOf, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

import type {
  AddCorrectionsResult,
  BenchmarkArena,
  BenchmarkCase,
  BenchmarkEstimate,
  BenchmarkExportFormat,
  BenchmarkOptions,
  BenchmarkPairNext,
  BenchmarkPickChoice,
  BenchmarkPickSummary,
  BenchmarkResult,
  BenchmarkRun,
  BenchmarkRunRequest,
  BenchmarkRunStarted,
  BenchmarkSet,
  BenchmarkTier,
  BuildSetRequest,
  BuildSetResult,
  CaseEdit,
  HardSubApplyResult,
  HardSubReadingEstimate,
  HardSubReadingResult,
  ClipImportResult,
  ClipNote,
  ClipRowPayload,
  ImportFormat,
  SourceLanguage,
  Sweep,
  SweepDecisionRow,
  SweepEstimate,
  SweepRequest,
  TitleCorrections,
  TranslateSweep,
  TranslateSweepEstimate,
  TranslateSweepRequest,
} from '../types/benchmark'

const BASE = '/api/benchmark'

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

interface BenchmarkRunDetail {
  run: BenchmarkRun
  results: BenchmarkResult[]
}

/** The query string for GET /cases: only the filters that are set. */
export function casesQuery(filters: { stage?: string | null; tier?: string | null; set_name?: string | null } = {}): string {
  const params = new URLSearchParams()
  if (filters.stage) params.set('stage', filters.stage)
  if (filters.tier) params.set('tier', filters.tier)
  // An empty set name is a real set ("no set"); the server treats it as "any", so it is left out.
  if (filters.set_name) params.set('set_name', filters.set_name)
  return queryOf(params)
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

// "Add a hard-sub test case" from a Review line: three pictures of the subtitle band across the line's time,
// cut from the title's video on the PC. The band defaults to the bottom 30% of the picture.
export const addHardSubCases = (dramaId: number, lineIds: number[], f?: Fetch) =>
  postJson<{ set_name: string; added: number; skipped: number; unclear: number }>(
    `${BASE}/dramas/${dramaId}/hard-sub-cases`, { line_ids: lineIds }, pcOnlyFetch(f))

// The latest hard-sub reading of a title by speaker: its progress, or the lines per speaker and the comparison
// with the transcript. 404 when none has run. PC only.
export const getHardSubReading = (dramaId: number, f?: Fetch) =>
  getJson<HardSubReadingResult>(`${BASE}/dramas/${dramaId}/hard-sub-reading`, f)

// Reads a title's burned-in captions by speaker group. The estimate spends nothing; starting needs confirm.
// PC only.
export const estimateHardSubReading = (dramaId: number, body: { engine: string; model?: string | null }, f?: Fetch) =>
  postJson<HardSubReadingEstimate>(`${BASE}/dramas/${dramaId}/hard-sub-reading/estimate`, body, pcOnlyFetch(f))

export const startHardSubReading = (dramaId: number, body: { engine: string; model?: string | null }, f?: Fetch) =>
  postJson<{ job_id: string; windows: number; estimated_usd: number }>(
    `${BASE}/dramas/${dramaId}/hard-sub-reading`, { ...body, confirm: true }, pcOnlyFetch(f))

// Puts the finished reading's speaker groups on the title's lines. PC only.
export const applyHardSubReading = (dramaId: number, f?: Fetch) =>
  postJson<HardSubApplyResult>(`${BASE}/dramas/${dramaId}/hard-sub-reading/apply`, {}, pcOnlyFetch(f))

// Reads pasted text only; nothing is downloaded.
export const importGoldenSet = (body: BenchmarkImportRequest, f?: Fetch) =>
  postJson<BenchmarkImportResult>(`${BASE}/import`, body, pcOnlyFetch(f))

// Cases from a title's lines: its reviewed (hand-edited or approved) translations are the references.
// dry_run only counts. PC only.
export const buildBenchmarkSet = (body: BuildSetRequest, f?: Fetch) =>
  postJson<BuildSetResult>(`${BASE}/sets/from-title`, body, pcOnlyFetch(f))

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
  return getJson<{ runs: BenchmarkRun[] }>(`${BASE}/runs${queryOf(params)}`, f)
}

export const getBenchmarkRun = (id: number, f?: Fetch) => getJson<BenchmarkRunDetail>(`${BASE}/runs/${id}`, f)

export function getBenchmarkArena(runIds: number[], f?: Fetch) {
  const params = new URLSearchParams()
  runIds.forEach((id) => params.append('run_ids', String(id)))
  return getJson<BenchmarkArena>(`${BASE}/arena?${params}`, f)
}

// ---- blind A/B: the owner picks the better of two runs' outputs --------------

const pairQuery = (runA: number, runB: number) => `run_a=${runA}&run_b=${runB}`

export const getBenchmarkPair = (runA: number, runB: number, f?: Fetch) =>
  getJson<BenchmarkPairNext>(`${BASE}/ab/next?${pairQuery(runA, runB)}`, f)

export const getBenchmarkPickSummary = (runA: number, runB: number, f?: Fetch) =>
  getJson<BenchmarkPickSummary>(`${BASE}/ab/summary?${pairQuery(runA, runB)}`, f)

export const saveBenchmarkPick = (ticket: string, choice: BenchmarkPickChoice, f?: Fetch) =>
  postJson<{ saved: boolean }>(`${BASE}/ab/pick`, { ticket, choice }, pcOnlyFetch(f))

// ---- the owner's clip set -------------------------------------------------

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

export const getCaseEdits = (id: number, f?: Fetch) => getJson<{ edits: CaseEdit[] }>(`${BASE}/cases/${id}/edits`, f)

// Removes a run and its results; its cases stay.
export const deleteBenchmarkRun = (id: number, f?: Fetch) =>
  postJson<{ deleted: boolean; id: number }>(`${BASE}/runs/${id}/delete`, { confirm: true }, pcOnlyFetch(f))

/** Download links: the setup, scores and each case's output for a run, or for runs compared on their shared cases. */
export const runExportUrl = (id: number, format: BenchmarkExportFormat) => apiUrl(`${BASE}/runs/${id}/export?format=${format}`)

export function arenaExportUrl(runIds: number[], format: BenchmarkExportFormat) {
  const params = new URLSearchParams()
  runIds.forEach((id) => params.append('run_ids', String(id)))
  params.set('format', format)
  return apiUrl(`${BASE}/arena/export?${params}`)
}

// ---- the settings sweep (services/benchmark_sweep_service.py) ----

export const getSweep = (f?: Fetch) => getJson<{ sweep: Sweep | null }>(`${BASE}/sweep`, f)

export const getSweepDecisions = (f?: Fetch) => getJson<{ decisions: SweepDecisionRow[] }>(`${BASE}/sweep/decisions`, f)

// Spends nothing: the halves, what the sweep covers, the passes it can skip and a rough time.
export const estimateSweep = (body: SweepRequest, f?: Fetch) => postJson<SweepEstimate>(`${BASE}/sweep/estimate`, body, f)

// Starts or resumes the sweep as the "benchmark_lab" job (poll it with useJob). PC only.
export const startSweep = (body: SweepRequest, f?: Fetch) =>
  postJson<{ job_id: string; sweep: Sweep }>(`${BASE}/sweep`, { ...body, confirm: true }, pcOnlyFetch(f))

// Approve saves the winner as a setting profile; both are recorded. PC only.
export const decideSweep = (decision: 'approve' | 'reject', reason: string, f?: Fetch) =>
  postJson<{ sweep: Sweep | null }>(`${BASE}/sweep/decision`, { decision, reason: reason || null, confirm: true }, pcOnlyFetch(f))

// ---- the translation settings sweep (services/benchmark_translate_sweep_service.py) ----

export const getTranslateSweep = (f?: Fetch) => getJson<{ sweep: TranslateSweep | null }>(`${BASE}/translate-sweep`, f)

export const getTranslateSweepDecisions = (f?: Fetch) =>
  getJson<{ decisions: SweepDecisionRow[] }>(`${BASE}/translate-sweep/decisions`, f)

// Spends nothing: the halves, what the sweep covers, the passes it can skip and what the paid ones would cost.
export const estimateTranslateSweep = (body: TranslateSweepRequest, f?: Fetch) =>
  postJson<TranslateSweepEstimate>(`${BASE}/translate-sweep/estimate`, body, f)

// Starts or resumes the sweep as the "benchmark_lab" job (poll it with useJob). PC only.
export const startTranslateSweep = (body: TranslateSweepRequest, f?: Fetch) =>
  postJson<{ job_id: string; sweep: TranslateSweep }>(`${BASE}/translate-sweep`, { ...body, confirm: true }, pcOnlyFetch(f))

// Approve saves the winner as a setting profile; both are recorded. PC only.
export const decideTranslateSweep = (decision: 'approve' | 'reject', reason: string, f?: Fetch) =>
  postJson<{ sweep: TranslateSweep | null }>(`${BASE}/translate-sweep/decision`, { decision, reason: reason || null, confirm: true }, pcOnlyFetch(f))
