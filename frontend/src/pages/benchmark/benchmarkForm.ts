/*
 * Pure helpers for the Benchmark Lab page (pages/Benchmark.tsx): the run
 * selection -> request body, whether Start may be pressed, number formatting,
 * metric wording, Arena groups and the compare selection. No React here, so
 * they are tested without a browser (benchmarkForm.test.ts).
 */
import { ApiError } from '../../api/client'
import type {
  BenchmarkConfig, BenchmarkEngineOption, BenchmarkEstimate, BenchmarkOptions, BenchmarkRun,
  BenchmarkRunRequest, BenchmarkSet, BenchmarkStage, BenchmarkTier,
} from '../../api/benchmark'
import { describeError, safeDetail } from '../../components/errorMessages'
import { humanize, humanizeValue, type BadgeTone } from '../../components/labels'

export const STAGE_LABELS: Record<string, string> = {
  translation: 'Translation',
  transcription: 'Transcription',
  ocr: 'OCR (text in images)',
}

export const TIER_LABELS: Record<string, string> = {
  public: 'Public',
  application: 'Application',
  regression: 'Regression',
}

export const TIER_HELP: Record<string, string> = {
  public: 'An imported public test set, e.g. a FLORES-200 slice.',
  application: 'Your own corrected translations.',
  regression: 'A line you fixed by hand and chose to keep as a test.',
}

const TIER_TONES: Record<string, BadgeTone> = { public: 'info', application: 'accent', regression: 'warn' }

export const OCR_LABELS: Record<string, string> = {
  tesseract: 'Tesseract',
  paddle: 'PaddleOCR',
  manga_ocr: 'Manga OCR',
  paddle_vl_manga: 'PaddleOCR-VL (manga)',
}

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  stopped_cap: 'Stopped at cap',
}

const RUN_STATUS_TONES: Record<string, BadgeTone> = {
  queued: 'neutral',
  running: 'info',
  done: 'ok',
  failed: 'bad',
  cancelled: 'warn',
  stopped_cap: 'warn',
}

export const stageLabel = (s: string | null | undefined) => (s ? STAGE_LABELS[s] ?? humanizeValue(s) : '—')
export const tierLabel = (t: string | null | undefined) => (t ? TIER_LABELS[t] ?? humanizeValue(t) : '—')
export const tierTone = (t: string | null | undefined): BadgeTone => TIER_TONES[t ?? ''] ?? 'neutral'
export const runStatusLabel = (s: string | null | undefined) => (s ? RUN_STATUS_LABELS[s] ?? humanizeValue(s) : '—')
export const runStatusTone = (s: string | null | undefined): BadgeTone => RUN_STATUS_TONES[s ?? ''] ?? 'neutral'
export const setDisplayName = (name: string | null | undefined) => (name ? name : '(no set)')

export const isRunActive = (r: Pick<BenchmarkRun, 'status'>) => r.status === 'queued' || r.status === 'running'

/** "Claude · claude-sonnet", "Whisper large-v3", "PaddleOCR". */
export function configLabel(stage: string | null | undefined, engine: string | null | undefined, model?: string | null): string {
  if (!engine) return '—'
  if (stage === 'transcription') return `Whisper ${model || engine}`
  if (stage === 'ocr') return OCR_LABELS[engine] ?? humanizeValue(engine)
  const name = humanize('engine', engine)
  return model ? `${name} · ${model}` : name
}

export const runConfigLabel = (r: Pick<BenchmarkRun, 'stage' | 'engine' | 'model'>) => configLabel(r.stage, r.engine, r.model)

/** 0.8234 -> "82.3%"; null -> "—". */
export function formatScore(score: number | null | undefined): string {
  if (score == null || !Number.isFinite(score)) return '—'
  return `${(score * 100).toFixed(1)}%`
}

/** A score difference in percentage points: "+3.2 pts", "−1.0 pts", "±0 pts". */
export function formatDelta(delta: number | null | undefined): string {
  if (delta == null || !Number.isFinite(delta)) return '—'
  const pts = Math.round(delta * 1000) / 10
  if (pts === 0) return '±0 pts'
  return `${pts > 0 ? '+' : '−'}${Math.abs(pts).toFixed(1)} pts`
}

export function deltaTone(delta: number | null | undefined): BadgeTone {
  if (delta == null || Math.abs(delta) < 0.0005) return 'neutral'
  return delta > 0 ? 'ok' : 'bad'
}

/** Dollars: "$0.00" for nothing, 4 places under a cent, else 2. */
export function formatCost(usd: number | null | undefined): string {
  if (usd == null || !Number.isFinite(usd)) return '—'
  if (usd === 0) return '$0.00'
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`
}

export function formatLatency(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  return `${seconds.toFixed(1)} s`
}

/** "2026-09-29T14:03:11.123" -> "2026-09-29 14:03" (stored in UTC; shown as stored). */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—'
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso)
  return m ? `${m[1]} ${m[2]}` : iso
}

/** One result's metric: never read a CER/WER score as translation quality. */
export function metricName(metric: string | null | undefined): string {
  if (metric === 'similarity') return 'similarity'
  if (metric === 'cer') return '1 − CER'
  if (metric === 'wer') return '1 − WER'
  return metric ? humanizeValue(metric) : ''
}

/** What a run's score means, by stage. */
export function metricNote(stage: string | null | undefined): string {
  if (stage === 'translation') return 'Score: text similarity to the reference translation (not a quality rating).'
  if (stage === 'transcription') return 'Score: 1 − CER (character error rate), or 1 − WER (word error rate) for space-separated languages.'
  if (stage === 'ocr') return 'Score: 1 − CER (character error rate) of the recognised text.'
  return ''
}

// ---- the run form ----

export interface RunSelection {
  stage: BenchmarkStage
  configs: BenchmarkConfig[]
  tier: BenchmarkTier | ''
  setName: string
  label: string
  promptVersion: string
}

/** The request body: blank filters and models are left out. */
export function runRequestBody(sel: RunSelection): BenchmarkRunRequest {
  const body: BenchmarkRunRequest = {
    stage: sel.stage,
    configs: sel.configs.map((c) => (c.model ? { engine: c.engine, model: c.model } : { engine: c.engine })),
  }
  if (sel.tier) body.tier = sel.tier
  if (sel.setName) body.set_name = sel.setName
  const label = sel.label.trim()
  if (label) body.label = label
  const pv = sel.promptVersion.trim()
  if (pv) body.prompt_version = pv
  return body
}

/** What an estimate depends on (not the label or prompt version): a changed key means estimate again. */
export function estimateKey(sel: RunSelection): string {
  const { stage, configs, tier, set_name } = runRequestBody(sel)
  return JSON.stringify({ stage, configs, tier: tier ?? null, set_name: set_name ?? null })
}

/** The first config for a stage (or a new one added to the list): the next engine not picked yet. */
export function defaultConfig(stage: BenchmarkStage, options: BenchmarkOptions, taken: BenchmarkConfig[] = []): BenchmarkConfig | null {
  const used = new Set(taken.map((c) => `${c.engine}|${c.model ?? ''}`))
  let candidates: BenchmarkConfig[]
  if (stage === 'transcription') candidates = options.whisper_sizes.map((s) => ({ engine: 'whisper', model: s }))
  else if (stage === 'ocr') candidates = options.ocr_backends.map((b) => ({ engine: b }))
  else {
    const usable = options.translation_engines.filter((e) => e.key_configured)
    candidates = [...usable, ...options.translation_engines.filter((e) => !e.key_configured)].map((e) => ({ engine: e.name }))
  }
  return candidates.find((c) => !used.has(`${c.engine}|${c.model ?? ''}`)) ?? null
}

/** Remembered configs still valid for today's options, else one default config. */
export function restoreConfigs(stage: BenchmarkStage, remembered: unknown, options: BenchmarkOptions): BenchmarkConfig[] {
  const valid = (c: BenchmarkConfig) => {
    if (stage === 'transcription') return c.engine === 'whisper' && !!c.model && options.whisper_sizes.includes(c.model)
    if (stage === 'ocr') return options.ocr_backends.includes(c.engine) && !c.model
    const e = options.translation_engines.find((x) => x.name === c.engine)
    return !!e && (!c.model || (e.models ?? []).includes(c.model))
  }
  const list = Array.isArray(remembered)
    ? remembered
        .filter((c): c is BenchmarkConfig => !!c && typeof c === 'object' && typeof (c as BenchmarkConfig).engine === 'string')
        .map((c) => (c.model ? { engine: c.engine, model: String(c.model) } : { engine: c.engine }))
        .filter(valid)
        .slice(0, options.max_configs)
    : []
  if (list.length) return list
  const d = defaultConfig(stage, options)
  return d ? [d] : []
}

/** Reasons the estimate can't be asked for yet (empty = it can). */
export function selectionProblems(sel: RunSelection, maxConfigs: number): string[] {
  const out: string[] = []
  if (!sel.configs.length) out.push('Pick at least one engine.')
  if (sel.configs.length > maxConfigs) out.push(`At most ${maxConfigs} engines at once.`)
  const keys = sel.configs.map((c) => `${c.engine}|${c.model ?? ''}`)
  if (new Set(keys).size !== keys.length) out.push('The same engine and model is picked twice.')
  return out
}

/** Translation engines picked that have no key yet (the server refuses to start them). */
export function enginesMissingKey(sel: RunSelection, engines: BenchmarkEngineOption[]): BenchmarkEngineOption[] {
  if (sel.stage !== 'translation') return []
  const picked = new Set(sel.configs.map((c) => c.engine))
  return engines.filter((e) => picked.has(e.name) && !e.key_configured)
}

export interface StartState {
  ok: boolean
  // Plain-English reasons Start is disabled, first one is the most useful.
  reasons: string[]
}

/** Start needs: the PC, a shown estimate for exactly this selection, the cap allowing it, keys, no run going. */
export function startState(args: {
  sel: RunSelection
  maxConfigs: number
  estimate: BenchmarkEstimate | null
  estimateFor: string | null
  pcRemote: boolean
  running: boolean
  missingKeys: BenchmarkEngineOption[]
}): StartState {
  const { sel, estimate, estimateFor, pcRemote, running, missingKeys } = args
  const reasons = [...selectionProblems(sel, args.maxConfigs)]
  if (pcRemote) reasons.push('Starting a run is PC only.')
  if (running) reasons.push('A benchmark run is already going.')
  for (const e of missingKeys) reasons.push(`${humanize('engine', e.name)} has no key. Set one in Settings.`)
  if (!estimate || estimateFor !== estimateKey(sel)) reasons.push('Estimate the cost of this selection first.')
  else {
    if (estimate.monthly_refusal) reasons.push(estimate.monthly_refusal)
    else if (estimate.estimate_above_cap) reasons.push("The estimate is more than what's left of this month's cap.")
  }
  return { ok: reasons.length === 0, reasons }
}

/** Sets a run can pick: named sets of this stage (and tier, when one is chosen). */
export function setOptions(sets: BenchmarkSet[], stage: string, tier: string): string[] {
  const names = sets
    .filter((s) => (s.stage ?? '') === stage && (!tier || s.tier === tier) && s.set_name)
    .map((s) => s.set_name)
  return [...new Set(names)].sort((a, b) => a.localeCompare(b))
}

/** How many cases a selection covers, from the set list (before the server's own count). */
export function casesInSelection(sets: BenchmarkSet[], stage: string, tier: string, setName: string): number {
  return sets
    .filter((s) => (s.stage ?? '') === stage && (!tier || s.tier === tier) && (!setName || s.set_name === setName))
    .reduce((n, s) => n + s.case_count, 0)
}

// ---- runs and the Arena ----

export interface ArenaGroup {
  group: string
  runIds: number[]
  label: string
  created: string | null
}

/** Runs started together (same arena_group), 2-4 of them, in the order they were run. */
export function arenaGroups(runs: BenchmarkRun[], max = 4): ArenaGroup[] {
  const byGroup = new Map<string, BenchmarkRun[]>()
  for (const r of runs) {
    if (!r.arena_group) continue
    const list = byGroup.get(r.arena_group) ?? []
    list.push(r)
    byGroup.set(r.arena_group, list)
  }
  const out: ArenaGroup[] = []
  for (const [group, list] of byGroup) {
    if (list.length < 2) continue
    const ordered = [...list].sort((a, b) => a.id - b.id).slice(0, max)
    const first = ordered[0]
    out.push({
      group,
      runIds: ordered.map((r) => r.id),
      label: first.label || `${stageLabel(first.stage)} arena`,
      created: first.created_at,
    })
  }
  return out
}

/** A name per compared run: its engine, plus its label (or number) when two runs share an engine. */
export function arenaRunNames(runs: Pick<BenchmarkRun, 'id' | 'label' | 'stage' | 'engine' | 'model'>[]): string[] {
  const names = runs.map(runConfigLabel)
  return names.map((n, i) => (names.filter((m) => m === n).length > 1 ? `${n} · ${runs[i].label || `run ${runs[i].id}`}` : n))
}

/** Tick or untick a run for comparing; at most `max` stay picked. */
export function toggleCompare(selected: number[], id: number, max = 4): number[] {
  if (selected.includes(id)) return selected.filter((x) => x !== id)
  if (selected.length >= max) return selected
  return [...selected, id]
}

/** Why "Compare in Arena" is disabled, or null when it can run. */
export function compareProblem(selected: number[], runs: BenchmarkRun[], max = 4): string | null {
  if (selected.length < 2) return `Tick 2 to ${max} runs to compare.`
  if (selected.length > max) return `At most ${max} runs at once.`
  const stages = new Set(selected.map((id) => runs.find((r) => r.id === id)?.stage ?? null))
  if (stages.size > 1) return 'Only runs of the same stage can be compared.'
  return null
}

// ---- errors ----

// Codes whose server message is a plain sentence written for people.
const PLAIN_CODES = ['invalid_input', 'unsupported_operation', 'conflict', 'not_found', 'dependency_unavailable']

/**
 * A server error as one plain sentence: the server's own text when it is a
 * readable sentence with nothing key- or path-like, else the generic line.
 */
export function plainError(err: unknown, opts: { pcOnly?: boolean } = {}): string {
  if (err instanceof ApiError && PLAIN_CODES.includes(err.code)) {
    const detail = safeDetail(err.message)
    if (detail) return detail
  }
  return describeError(err, { pcOnly: opts.pcOnly }).title
}
