// Benchmark Lab shapes (api/benchmark_schemas.py); the calls that use them are in api/benchmark.ts.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type BenchmarkStage = 'translation' | 'transcription' | 'ocr'
export type BenchmarkTier = 'public' | 'application' | 'regression'
export type SourceLanguage = 'zh' | 'ja' | 'ko'
export type ImportFormat = 'jsonl' | 'tsv'

export type BenchmarkEngineOption = Schemas['BenchmarkEngineOption']

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

/** A translation arm's options: the cases of a title run together, in order, the way a title is translated. A
 * field left out takes what a title's Translate starts with (BenchmarkOptions.translate_choices). */
export type BenchmarkTranslateOptions = Schemas['BenchmarkTranslateOptions']

export type BenchmarkTranslateChoices = Schemas['BenchmarkTranslateChoices']

/** A run's line timing over the cases that have reference times; only `cases` (0) when none do. */
export type BenchmarkTimingSummary = Schemas['BenchmarkTimingSummary']

export type BenchmarkCaseTiming = Schemas['BenchmarkCaseTiming']

export type BenchmarkOptions = Schemas['BenchmarkOptions']

export type BenchmarkCase = Schemas['BenchmarkCase']

export type BenchmarkSet = Schemas['BenchmarkSet']

export interface BenchmarkConfig {
  engine: string
  model?: string
  /** Transcription only, and only the fields changed from the app's defaults. */
  options?: BenchmarkTranscribeOptions
  /** Translation only: present (even empty) = translate a title's cases together; only the fields changed from
   * a title's defaults. Sent as `options`. */
  translate?: BenchmarkTranslateOptions
}

/** A config as the server takes it. */
export interface BenchmarkRequestConfig {
  engine: string
  model?: string
  options?: BenchmarkTranscribeOptions | BenchmarkTranslateOptions
}

export type BenchmarkRunRequest = Schemas['BenchmarkRunRequest']

/** One output's judge scores, each 0-1. */
export type BenchmarkJudgeScore = Schemas['BenchmarkJudgeScore']

export type BenchmarkJudgeSummary = Schemas['BenchmarkJudgeSummary']

export type BenchmarkEstimate = Schemas['BenchmarkEstimate']

export type BenchmarkRunStarted = Schemas['BenchmarkRunStarted']

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

export type BenchmarkRun = Schemas['BenchmarkRun']

/** How often the owner picked this output over another run's (blind A/B). */
export type BenchmarkPickTotals = Schemas['BenchmarkPickTotals']

export type BenchmarkResult = Schemas['BenchmarkResult']

/** Cases an Arena comparison skipped, and why. */
export type BenchmarkLeftOut = Schemas['BenchmarkLeftOut']

/** A single run's results are shown in the same shape without `left_out`, which only a comparison has. */
export type BenchmarkArena = Omit<Schemas['BenchmarkArena'], 'left_out'> & {
  left_out?: BenchmarkLeftOut | null
}

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

/** Two outputs for one case in random order; nothing here names the run behind either. */
export type BenchmarkPairNext = Schemas['BenchmarkPairNext']

export type BenchmarkPickChoice = 'left' | 'right' | 'tie'

export type BenchmarkPickSummary = Schemas['BenchmarkPickSummary']

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

export interface CaseEdit {
  field: string
  old_text: string | null
  new_text: string | null
  version: number
  edited_at: string | null
}

export type BenchmarkExportFormat = 'csv' | 'json'

export type SweepValue = boolean | number
export type SweepLanguage = SourceLanguage

export interface SweepRequest {
  set_name: string
  language: SweepLanguage
  use_gpu?: boolean | null
}

interface SweepModel {
  engine: string
  model: string | null
  label: string
  included: boolean
  reason: string | null
}

interface SweepKnob {
  key: string
  label: string
  values: SweepValue[]
  included: boolean
  reason: string | null
}

export interface SweepEstimate {
  set_name: string
  language: SweepLanguage
  use_gpu: boolean
  tuning_cases: number
  holdout_cases: number
  tuning_titles: number
  holdout_titles: number
  audio_seconds: number
  durations_guessed: boolean
  small_set: boolean
  min_set_cases: number
  models: SweepModel[]
  knobs: SweepKnob[]
  not_swept: string[]
  passes_done: number
  passes_to_run: number
  estimated_seconds: number
  estimated_cost_usd: number
  monthly_cap_usd: number
  monthly_refusal: string | null
}

interface SweepSetup {
  engine: string
  model: string | null
  changes: Record<string, SweepValue | string>
  label: string
}

export interface SweepStep {
  step: string
  value: SweepValue | string | null
  half: 'tuning' | 'holdout'
  setup: string
  run_id: number | null
  score: number | null
  errors: number | null
  kept: boolean | null
}

interface SweepTag {
  tag: string
  cases: number
  default_score: number | null
  winner_score: number | null
  delta: number | null
  too_small: boolean
}

interface SweepHoldout {
  default_score: number | null
  winner_score: number | null
  delta: number | null
  default_run_id: number
  winner_run_id: number
  default_errors: number
  winner_errors: number
}

type SweepState = 'keep_default' | 'adopt' | 'not_confirmed'

export interface SweepRecommendation {
  state: SweepState
  text: string
  noise: number | null
  margin: number
  margin_rule: string
  default: SweepSetup
  winner: SweepSetup
  tuning: { default_score: number | null; winner_score: number | null }
  holdout: SweepHoldout | null
  tags: SweepTag[]
  steps: SweepStep[]
  case_counts: { tuning: number; holdout: number }
  small_set: boolean
}

/** One title setting the approved profile writes when it is applied to a title. */
interface SweepApplies {
  key: string
  label: string
  value: string
}

interface SweepDecision {
  decision: 'promoted' | 'rejected'
  reason: string
  decided_at: string | null
  decided_by?: string | null
  profile: { id: number; name: string } | null
  applies?: SweepApplies[] | null
}

export type SweepStatus = 'queued' | 'running' | 'done' | 'cancelled' | 'failed' | 'interrupted'

export interface Sweep {
  id: string | null
  set_name: string
  language: SweepLanguage
  use_gpu: boolean | null
  status: SweepStatus
  started_at: string | null
  finished_at: string | null
  note: string | null
  passes_run: number
  passes_reused: number
  recommendation: SweepRecommendation | null
  decision: SweepDecision | null
  /** While the winner waits for a decision: what approving it would let a title get. */
  applies?: SweepApplies[] | null
  estimate: Record<string, number> | null
}

export interface SweepDecisionRow {
  decision: string
  reason: string
  decided_at: string | null
  language: string | null
  setup: string
}

export interface TranslateSweepRequest {
  set_name: string
  language: SweepLanguage
  engine: string
  model?: string | null
  series_id?: number | null
}

interface TranslateSweepKnob {
  key: string
  label: string
  values: (boolean | number | string | null)[]
  included: boolean
  reason: string | null
}

export interface TranslateSweepEstimate {
  set_name: string
  language: SweepLanguage
  engine: string
  model: string | null
  series_id: number | null
  tuning_cases: number
  holdout_cases: number
  tuning_titles: number
  holdout_titles: number
  small_set: boolean
  min_set_cases: number
  models: SweepModel[]
  knobs: TranslateSweepKnob[]
  not_swept: string[]
  passes_done: number
  passes_to_run: number
  estimated_cost_usd: number
  /** False for a free engine: the monthly cap doesn't apply. */
  cap_applies: boolean
  monthly_cap_usd: number
  month_spend_usd: number
  remaining_usd: number | null
  monthly_refusal: string | null
  estimate_above_cap: boolean
}

export interface TranslateSweep extends Sweep {
  engine: string
  model: string | null
  series_id: number | null
}

export interface HardSubReadingLine { start: number; end: number; text: string }

export interface HardSubReadingOutcome {
  groups: { key: string; lines: HardSubReadingLine[] }[]
  windows_read: number
  windows_total: number
  unreadable: number
  unclear_pictures: number
  failed_reads: number
  cost_usd: number
  /** "cap", "cancelled" or "failed" when the reading ended early. */
  stopped: string | null
  note: string | null
  comparison: {
    groups: { key: string; characters: number; recall: number | null }[]
    recall: number | null
    precision: number | null
    label_overlap: Record<string, Record<string, number>>
    label_match: number | null
  }
}

export interface HardSubReadingResult {
  status: string
  progress: number | null
  message: string | null
  /** null while the reading is still going. */
  result: HardSubReadingOutcome | null
}

export interface HardSubReadingEstimate {
  engine: string
  model: string | null
  windows: number
  speaker_groups: number
  reads_at_most: number
  estimated_usd: number
  cap_applies: boolean
  monthly_cap_usd: number | null
  cap_left_usd: number | null
  fits_cap: boolean
}

export interface HardSubApplyResult {
  labelled: number
  kept_manual: number
  unplaced: number
  speakers: string[]
}
