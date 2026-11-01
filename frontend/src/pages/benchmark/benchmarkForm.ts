/*
 * Pure helpers for the Benchmark Lab page (pages/Benchmark.tsx): the run
 * selection -> request body, whether Start may be pressed, number formatting,
 * metric wording, Arena groups and the compare selection. No React here, so
 * they are tested without a browser (benchmarkForm.test.ts).
 */
import { ApiError } from '../../api/client'
import type { BenchmarkConfig, BenchmarkLeftOut, BenchmarkTranscribeOptions, BenchmarkEngineOption, BenchmarkEstimate, BenchmarkJudgeScore, BenchmarkJudgeSummary, BenchmarkOptions, BenchmarkRequestConfig, BenchmarkRun, BenchmarkRunRequest, BenchmarkSet, BenchmarkStage, BenchmarkTier, ScriptCounts, BenchmarkCaseTiming, BenchmarkTimingSummary } from '../../types/benchmark'
import { describeError, safeDetail } from '../../components/errorMessages'
import { humanize, humanizeValue, type BadgeTone } from '../../components/labels'
import { readSectionOpen, type StorageLike } from '../../components/sectionStorage'
import { engineGate, type EngineGate } from '../workspace/stages/engineReady'
import { isActiveStatus, type JobRecord } from '../../types/jobs'
import { BENCH_DEFAULT_OPEN, type BenchSectionId } from './benchmarkHelp'
import { cleanTranslate, translateChangesLabel } from './translateOptions'

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

/** Recognisers besides Whisper (the server's transcription_backends names them too). */
const RECOGNISER_LABELS: Record<string, string> = { qwen3_asr_vad: 'Qwen3-ASR', anime_whisper: 'anime-whisper' }

export const OCR_LABELS: Record<string, string> = {
  tesseract: 'Tesseract',
  paddle: 'PaddleOCR',
  manga_ocr: 'Manga OCR',
  paddle_vl_manga: 'PaddleOCR-VL (manga)',
}

const RUN_STATUS_LABELS: Record<string, string> = {
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
export const benchmarkTierLabel = (t: string | null | undefined) => (t ? TIER_LABELS[t] ?? humanizeValue(t) : '—')
export const tierTone = (t: string | null | undefined): BadgeTone => TIER_TONES[t ?? ''] ?? 'neutral'
export const runStatusLabel = (s: string | null | undefined) => (s ? RUN_STATUS_LABELS[s] ?? humanizeValue(s) : '—')
export const runStatusTone = (s: string | null | undefined): BadgeTone => RUN_STATUS_TONES[s ?? ''] ?? 'neutral'
export const setDisplayName = (name: string | null | undefined) => (name ? name : '(no set)')

export const isRunActive = (r: Pick<BenchmarkRun, 'status'>) => isActiveStatus(r.status)

/** "Claude · claude-sonnet", "Whisper large-v3", "PaddleOCR". */
export function configLabel(stage: string | null | undefined, engine: string | null | undefined, model?: string | null): string {
  if (!engine) return '—'
  if (stage === 'transcription') return engine === 'whisper' ? `Whisper ${model || engine}` : (RECOGNISER_LABELS[engine] ?? `Whisper ${model || engine}`)
  if (stage === 'ocr' && engine in OCR_LABELS) return OCR_LABELS[engine]
  const name = humanize('engine', engine)
  return model ? `${name} · ${model}` : name
}

/** Short names for the options a transcription arm changed, as shown beside its model. */
const CHANGE_LABELS: Record<string, (v: unknown) => string> = {
  beam_size: (v) => `beam ${v}`,
  min_silence_ms: (v) => `min silence ${v} ms`,
  vad_threshold: (v) => `VAD ${v}`,
  sensitivity_preset: (v) => `${v} sensitivity`,
  hallucination_silence_sec: (v) => (v === 0 ? 'no hallucination skip' : `hallucination skip ${v} s`),
  whisper_repeat_guard: (v) => (v ? 'repeat guard' : 'no repeat guard'),
  difficult_audio: (v) => (v ? 'difficult audio' : 'no difficult audio'),
  separate_vocals_first: (v) => (v ? 'vocals separated' : 'vocals not separated'),
  initial_prompt: () => 'initial prompt',
}

/** "beam 1, min silence 600 ms" for the options a run changed from the app's defaults; '' for a default run. */
export function runChangesLabel(r: { context_settings?: Record<string, unknown> }): string {
  const translated = translateChangesLabel(r.context_settings?.translate_changes)
  if (translated) return translated
  const changes = r.context_settings?.transcribe_changes
  if (!changes || typeof changes !== 'object') return ''
  return TRANSCRIBE_OPTION_KEYS.filter((k) => k in (changes as object))
    .map((k) => CHANGE_LABELS[k]((changes as Record<string, unknown>)[k]))
    .join(', ')
}

type RunLabelFields = Pick<BenchmarkRun, 'stage' | 'engine' | 'model'> & { context_settings?: Record<string, unknown> }

export const runConfigLabel = (r: RunLabelFields) => {
  const changes = runChangesLabel(r)
  return changes ? `${configLabel(r.stage, r.engine, r.model)} (${changes})` : configLabel(r.stage, r.engine, r.model)
}

/** The run's own label, or null when it only repeats the engine and model shown beside it. */
export const runExtraLabel = (r: RunLabelFields & Pick<BenchmarkRun, 'label'>) =>
  r.label && r.label.trim() !== runConfigLabel(r) ? r.label : null

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

/** "Traditional 3 / Simplified 0 / mixed 1 / neutral 2 lines · 41 Traditional, 0 Simplified chars"; '' when not a Chinese transcript. */
/** A run's line timing in one line, or '' when no case had reference times. */
export function timingLine(t: BenchmarkTimingSummary | undefined): string {
  if (!t || !t.cases) return ''
  const ms = (v: number | null | undefined) => (v == null ? 'n/a' : `${v} ms`)
  const parts = [
    `Timing on ${t.cases} ${t.cases === 1 ? 'line' : 'lines'}: start ${ms(t.start_median_ms)}, end ${ms(t.end_median_ms)} off (median)`,
    `${Math.round((t.within ?? 0) * 100)}% within 0.3 s`,
    `${t.merged ?? 0} merged`, `${t.split ?? 0} split`, `${t.missed ?? 0} missed`,
  ]
  if (t.silence != null) parts.push(`${t.silence} in silence`)
  return parts.join(' · ')
}

/** One case's timing result, or '' without one. */
export function caseTimingLine(t: BenchmarkCaseTiming | null | undefined): string {
  if (!t) return ''
  if (t.missed) return 'Timing: line missed'
  const s = (v: number | null) => (v == null ? 'n/a' : `${Math.round(v * 1000)} ms`)
  const flags = [t.merged ? 'merged' : '', t.split ? 'split' : ''].filter(Boolean)
  return `Timing: start ${s(t.start_error_s)}, end ${s(t.end_error_s)} off${flags.length ? ` · ${flags.join(', ')}` : ''}`
}

export function scriptCountsLine(c: ScriptCounts | null | undefined): string {
  if (!c) return ''
  return (
    `Lines: ${c.traditional_lines} Traditional / ${c.simplified_lines} Simplified / ${c.mixed_lines} mixed / ${c.neutral_lines} neutral · ` +
    `Characters: ${c.traditional_chars} Traditional, ${c.simplified_chars} Simplified`
  )
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
  if (metric === 'chrf') return 'chrF'
  if (metric === 'cer') return '1 − CER'
  if (metric === 'wer') return '1 − WER'
  return metric ? humanizeValue(metric) : ''
}

/** What a run's score means, by stage. */
export function metricNote(stage: string | null | undefined): string {
  if (stage === 'translation') return 'Score: chrF (character n-gram F-score) against the reference translation, or plain text similarity on runs made without sacrebleu (not a quality rating).'
  if (stage === 'transcription') return 'Score: 1 − CER (character error rate), or 1 − WER (word error rate) for space-separated languages.'
  if (stage === 'ocr') return 'Score: 1 − CER (character error rate) of the recognised text.'
  return ''
}

/** Set when an arena lines up one case's translation scores from both metrics:
 * chrF and the older similarity ratio sit on different scales. */
export function mixedMetricNote(rows: { results: ({ metric: string | null } | null)[] }[]): string {
  const mixed = rows.some((row) => {
    const seen = new Set(row.results.map((r) => r?.metric).filter((m) => m === 'chrf' || m === 'similarity'))
    return seen.size > 1
  })
  return mixed ? 'Some of these runs were scored with chrF and some with the older similarity ratio. The scales differ, so re-run the older runs to compare like with like.' : ''
}

/** Set when an arena lines up one case's CER/WER scores from both scorers
 * (jiwer and the built-in one): their numbers can differ for the same
 * output. Checked per case, since jiwer runs fall back to the built-in
 * scorer for a case with nothing left to score after normalisation (every
 * run does the same for that case). Results with no scorer recorded
 * predate jiwer and are built-in. */
export function mixedScorerNote(rows: { results: ({ metric: string | null; scorer?: string | null } | null)[] }[]): string {
  const mixed = rows.some((row) => {
    const seen = new Set<string>()
    for (const r of row.results)
      if (r && (r.metric === 'cer' || r.metric === 'wer')) seen.add(r.scorer === 'jiwer' ? 'jiwer' : 'builtin')
    return seen.size > 1
  })
  return mixed
    ? 'Some of these runs were scored with jiwer and some with the built-in scorer, so their scores can differ for the same output. Re-run the older runs to compare like with like.'
    : ''
}

// ---- the run form ----

/** The engine picked to judge a translation run; model '' = the engine's default. */
export interface JudgePick {
  engine: string
  model: string
}

export interface RunSelection {
  stage: BenchmarkStage
  configs: BenchmarkConfig[]
  tier: BenchmarkTier | ''
  setName: string
  label: string
  promptVersion: string
  /** Translation runs only; null or absent = no judge. */
  judge?: JudgePick | null
  /** The "judge with a model I'm also testing" tick. */
  allowSameJudge?: boolean
}

/** Tested configs the judge may be the same model as: the same engine with the same model, or either on its default
 * (the page can't see an engine's default model; the server decides exactly and refuses without the tick). */
export function judgeOverlap(sel: Pick<RunSelection, 'stage' | 'configs' | 'judge'>): BenchmarkConfig[] {
  const j = sel.judge
  if (sel.stage !== 'translation' || !j) return []
  return sel.configs.filter((c) => c.engine === j.engine && (!c.model || !j.model || c.model === j.model))
}

/** One config as sent: a blank model is left out, and so are options on a stage that has none or that are all defaults. */
function requestConfig(stage: BenchmarkStage, c: BenchmarkConfig): BenchmarkConfig {
  const out: BenchmarkConfig = c.model ? { engine: c.engine, model: c.model } : { engine: c.engine }
  const options = stage === 'transcription' ? cleanOptions(c.options) : undefined
  if (options) out.options = options
  const translate = stage === 'translation' ? cleanTranslate(c.translate) : undefined
  if (translate) out.translate = translate
  return out
}

/** A config as the server takes it: a translation arm's options go as `options`, even when none changed. */
function wireConfig(stage: BenchmarkStage, c: BenchmarkConfig): BenchmarkRequestConfig {
  const { translate, ...rest } = requestConfig(stage, c)
  return translate ? { ...rest, options: translate } : rest
}

/** The options a person changed, in the order the form shows them; undefined when none was. */
function cleanOptions(options: unknown): BenchmarkTranscribeOptions | undefined {
  if (!options || typeof options !== 'object') return undefined
  const given = options as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const key of TRANSCRIBE_OPTION_KEYS) {
    const v = given[key]
    if (v === undefined || v === null || (typeof v === 'string' && !v.trim()) || (typeof v === 'number' && !Number.isFinite(v))) continue
    out[key] = typeof v === 'string' ? v.trim() : v
  }
  return Object.keys(out).length ? (out as BenchmarkTranscribeOptions) : undefined
}

const TRANSCRIBE_OPTION_KEYS = [
  'beam_size', 'min_silence_ms', 'vad_threshold', 'sensitivity_preset', 'hallucination_silence_sec',
  'whisper_repeat_guard', 'difficult_audio', 'separate_vocals_first', 'initial_prompt',
  'speech_pad_ms', 'refine_timing', 'split_max_seconds', 'split_max_chars',
] as const

/** An option as the form shows it: its changed value, else the app's default. */
export function optionValue<K extends keyof BenchmarkTranscribeOptions>(
  config: BenchmarkConfig, defaults: Required<BenchmarkTranscribeOptions>, key: K,
): Required<BenchmarkTranscribeOptions>[K] {
  const v = config.options?.[key]
  return (v === undefined ? defaults[key] : v) as Required<BenchmarkTranscribeOptions>[K]
}

/** `config` with one option set; setting it back to the default drops it, so a default arm sends nothing. */
export function withOption(
  config: BenchmarkConfig, defaults: Required<BenchmarkTranscribeOptions>, key: keyof BenchmarkTranscribeOptions, value: unknown,
): BenchmarkConfig {
  const next: Record<string, unknown> = { ...(config.options ?? {}) }
  if (value === undefined || value === defaults[key]) delete next[key]
  else next[key] = value
  const { options: _dropped, ...rest } = config
  const options = cleanOptions(next)
  return options ? { ...rest, options } : rest
}

/** A config's identity: two arms are the same only when engine, model and changed options all match. */
export function configKey(c: BenchmarkConfig): string {
  const translate = cleanTranslate(c.translate)
  return `${c.engine}|${c.model ?? ''}|${JSON.stringify(cleanOptions(c.options) ?? null)}${translate ? `|${JSON.stringify(translate)}` : ''}`
}

/** The label of a transcription pick: a recogniser's own name, else the Whisper size. */
export function recogniserLabel(options: BenchmarkOptions, config: BenchmarkConfig): string {
  return options.transcription_backends.find((b) => b.name === config.engine)?.label ?? config.model ?? config.engine
}

/** The request body: blank filters and models are left out. */
export function runRequestBody(sel: RunSelection): BenchmarkRunRequest {
  const body: BenchmarkRunRequest = {
    stage: sel.stage,
    configs: sel.configs.map((c) => wireConfig(sel.stage, c)),
  }
  if (sel.tier) body.tier = sel.tier
  if (sel.setName) body.set_name = sel.setName
  const label = sel.label.trim()
  if (label) body.label = label
  const pv = sel.promptVersion.trim()
  if (pv) body.prompt_version = pv
  if (sel.stage === 'translation' && sel.judge) {
    body.judge = { engine: sel.judge.engine }
    if (sel.judge.model) body.judge.model = sel.judge.model
    if (sel.allowSameJudge && judgeOverlap(sel).length) body.judge.allow_same_model = true
  }
  return body
}

/** What an estimate depends on (not the label or prompt version): a changed key means estimate again.
 *  cases: how many cases the selection covers now; importing a bigger set must not leave a small estimate standing. */
export function estimateKey(sel: RunSelection, cases?: number): string {
  const { stage, configs, tier, set_name, judge } = runRequestBody(sel)
  return JSON.stringify({ stage, configs, tier: tier ?? null, set_name: set_name ?? null, judge: judge ?? null, cases: cases ?? null })
}

/** Whisper models for every case language, and those the server does not recommend for some language (with why).
 *  A limited model is never picked by default; the server refuses it on a case it does not fit. */
export function whisperChoiceGroups(options: BenchmarkOptions): { everywhere: string[]; limited: { size: string; reasons: string[] }[] } {
  const exclusions = options.whisper_exclusions ?? {}
  const everywhere: string[] = []
  const limited: { size: string; reasons: string[] }[] = []
  for (const size of options.whisper_sizes) {
    const reasons = [...new Set(Object.values(exclusions[size] ?? {}))]
    if (reasons.length) limited.push({ size, reasons })
    else everywhere.push(size)
  }
  return { everywhere, limited }
}

/** The first config for a stage (or a new one added to the list): the next engine not picked yet. */
export function defaultConfig(stage: BenchmarkStage, options: BenchmarkOptions, taken: BenchmarkConfig[] = []): BenchmarkConfig | null {
  const used = new Set(taken.map((c) => `${c.engine}|${c.model ?? ''}`))
  let candidates: BenchmarkConfig[]
  // The other recognisers are picked by hand: they run only when installed, so Add never lands on one.
  if (stage === 'transcription') candidates = whisperChoiceGroups(options).everywhere.map((s) => ({ engine: 'whisper', model: s }))
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
    if (stage === 'transcription') {
      if (c.engine === 'whisper') return !!c.model && options.whisper_sizes.includes(c.model)
      return !c.model && options.transcription_backends.some((b) => b.name === c.engine)
    }
    const e = options.translation_engines.find((x) => x.name === c.engine)
    if (stage === 'ocr') {
      if (options.vision_engines.includes(c.engine)) return !!e && (!c.model || (e.models ?? []).includes(c.model))
      return options.ocr_backends.includes(c.engine) && !c.model
    }
    return !!e && (!c.model || (e.models ?? []).includes(c.model))
  }
  const list = Array.isArray(remembered)
    ? remembered
        .filter((c): c is BenchmarkConfig => !!c && typeof c === 'object' && typeof (c as BenchmarkConfig).engine === 'string')
        .map((c) => requestConfig(stage, c.model ? { ...c, model: String(c.model) } : { ...c, model: undefined }))
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
  const keys = sel.configs.map(configKey)
  if (new Set(keys).size !== keys.length) out.push('The same engine and model is picked twice.')
  return out
}

/** Translation engines picked (tested or judging) that have no key yet (the server refuses to start them). */
export function enginesMissingKey(sel: RunSelection, engines: BenchmarkEngineOption[]): BenchmarkEngineOption[] {
  if (sel.stage === 'transcription') return []
  const picked = new Set(sel.configs.map((c) => c.engine))
  if (sel.judge) picked.add(sel.judge.engine)
  return engines.filter((e) => picked.has(e.name) && !e.key_configured)
}

/**
 * Why an engine that cannot run cannot: its key, its SDK or Ollama. The list reports one flag
 * (key_configured, which for Ollama means "answers"), so the cause comes from the same gate the Translate buttons use.
 */
export function engineBlock(e: BenchmarkEngineOption): EngineGate | null {
  return engineGate([e], e.name, e.name === 'ollama' ? e.key_configured : null)
}

/** The reason Start is off for an engine that cannot run. */
export function engineBlockReason(e: BenchmarkEngineOption): string {
  const gate = engineBlock(e)
  return gate && gate.id !== 'engine_key' ? gate.reason : `${humanize('engine', e.name)} has no key. Set one in Settings.`
}

const JUDGE_SAME_MODEL_REASON = 'Tick the box to judge with a model you are also testing, or pick another judge.'

export const JUDGE_SAME_MODEL_WARNING =
  'This judge may be the same engine and model as one being tested. A model tends to rate its own wording highly, so its judge scores for that engine are biased. Pick another judge, or tick the box to accept this.'

/** 0.9 -> "90"; the judge's three scores are shown as whole numbers out of 100. */
const pct = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : String(Math.round(v * 100)))

/** "90% (accuracy 90 · tone 80 · natural 70)" for one result. */
export function formatJudgeScore(j: BenchmarkJudgeScore | null | undefined): string {
  if (!j) return ''
  return `${formatScore(j.overall)} (accuracy ${pct(j.accuracy)} · tone ${pct(j.tone)} · natural ${pct(j.naturalness)})`
}

/** A run's judge line: average, how many cases it scored, and what the judge cost. */
export function judgeRunLine(j: BenchmarkJudgeSummary | null | undefined): string {
  if (!j) return ''
  const avg = j.average?.overall
  const parts = [avg != null ? `Judge ${formatScore(avg)}` : 'Judge —', `${j.scored} scored`, `judge cost ${formatCost(j.cost_usd)}`]
  if (j.status && j.status !== 'done') parts.push(runStatusLabel(j.status).toLowerCase())
  return parts.join(' · ')
}

interface StartState {
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
  /** The selection's current case count, when the estimate key was made with it. */
  caseCount?: number
  pcRemote: boolean
  running: boolean
  missingKeys: BenchmarkEngineOption[]
}): StartState {
  const { sel, estimate, estimateFor, pcRemote, running, missingKeys } = args
  const reasons = [...selectionProblems(sel, args.maxConfigs)]
  if (pcRemote) reasons.push('Starting a run is PC only.')
  if (running) reasons.push('A benchmark run is already going.')
  for (const e of missingKeys) reasons.push(engineBlockReason(e))
  if (judgeOverlap(sel).length && !sel.allowSameJudge) reasons.push(JUDGE_SAME_MODEL_REASON)
  if (!estimate || estimateFor !== estimateKey(sel, args.caseCount)) reasons.push('Estimate the cost of this selection first.')
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

/** How many cases a selection covers, from the set list (before the server's own count): excluded cases are not run. */
export function casesInSelection(sets: BenchmarkSet[], stage: string, tier: string, setName: string): number {
  return sets
    .filter((s) => (s.stage ?? '') === stage && (!tier || s.tier === tier) && (!setName || s.set_name === setName))
    .reduce((n, s) => n + s.case_count - (s.excluded ?? 0), 0)
}

// ---- runs and the Arena ----

interface ArenaGroup {
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

// ---- "Compare in Benchmark Lab" links (Model health on Diagnostics) ----

/**
 * The raw `compare` query value: "engine:model,engine:model". Each engine and
 * model is URI-encoded, so a model with ":" or "/" in it ("qwen3:8b") stays
 * one part; the separators are left literal.
 */
export function compareParam(configs: BenchmarkConfig[]): string {
  const enc = encodeURIComponent
  return configs.map((c) => (c.model ? `${enc(c.engine)}:${enc(c.model)}` : enc(c.engine))).join(',')
}

/** Reads compareParam's format back; malformed parts are skipped. */
export function parseCompareParam(raw: string | null | undefined): BenchmarkConfig[] {
  if (!raw) return []
  const out: BenchmarkConfig[] = []
  for (const part of raw.split(',').slice(0, 8)) {
    const i = part.indexOf(':')
    try {
      const engine = decodeURIComponent(i < 0 ? part : part.slice(0, i)).trim()
      const model = i < 0 ? '' : decodeURIComponent(part.slice(i + 1)).trim()
      if (engine) out.push(model ? { engine, model } : { engine })
    } catch {
      // A bad %-escape: skip this part.
    }
  }
  return out
}

export interface ComparePrefill {
  configs: BenchmarkConfig[]
  // Plain sentences about anything that couldn't be set up as asked.
  notes: string[]
}

/**
 * The translation configs a compare link asks for, checked against today's
 * options: an unknown engine or a model the app doesn't offer is left out
 * (with a note); an engine without a model choice runs its built-in model.
 */
export function comparePrefill(wanted: BenchmarkConfig[], options: BenchmarkOptions): ComparePrefill {
  const configs: BenchmarkConfig[] = []
  const notes: string[] = []
  const seen = new Set<string>()
  for (const c of wanted) {
    const label = configLabel('translation', c.engine, c.model)
    const engine = options.translation_engines.find((e) => e.name === c.engine)
    if (!engine) {
      notes.push(`${label} isn't an engine the Benchmark Lab can run, so it was left out.`)
      continue
    }
    let next: BenchmarkConfig = { engine: c.engine }
    if (c.model && engine.models === null) {
      notes.push(`${humanize('engine', c.engine)} has no model choice here, so it runs its built-in model.`)
    } else if (c.model && !engine.models?.includes(c.model)) {
      notes.push(`${label} isn't offered in this app any more, so it was left out.`)
      continue
    } else if (c.model) next = { engine: c.engine, model: c.model }
    const key = `${next.engine}|${next.model ?? ''}`
    if (seen.has(key)) continue
    if (configs.length >= options.max_configs) {
      notes.push(`${label} was left out: at most ${options.max_configs} engines at once.`)
      continue
    }
    seen.add(key)
    configs.push(next)
  }
  return { configs, notes }
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

// ---- page sections ----

export function benchSectionStorageKey(id: BenchSectionId): string {
  return `benchmark.card.${id}`
}

/** The remembered open state of a page section, else its default. */
export function benchSectionOpen(storage: StorageLike | null, id: BenchSectionId): boolean {
  return readSectionOpen(storage, benchSectionStorageKey(id), BENCH_DEFAULT_OPEN[id])
}

/** The one-line status a folded section shows while the benchmark job runs. */
export function runningStatus(job: JobRecord | null): string {
  const pct = job?.progress != null ? Math.round(job.progress * 100) : null
  return `Running${pct != null ? ` · ${pct}%` : '…'}`
}

/** What an Arena comparison covered and what it left out, so a score is never read as covering more than it did. */
export function leftOutLine(compared: number, left: BenchmarkLeftOut | null | undefined): string {
  const head = `Compared on the ${compared} ${compared === 1 ? 'case' : 'cases'} every run scored against the same reference.`
  if (!left || left.total === 0) return head
  const parts = [
    left.edited && `${left.edited} edited between the runs`,
    left.excluded && `${left.excluded} excluded`,
    left.removed && `${left.removed} deleted`,
    left.not_in_every_run && `${left.not_in_every_run} not scored in every run`,
  ].filter(Boolean)
  return `${head} ${left.total} left out: ${parts.join(', ')}.`
}
