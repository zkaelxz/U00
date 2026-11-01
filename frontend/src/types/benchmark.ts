// Benchmark Lab shapes (api/benchmark_schemas.py); the calls that use them are in api/benchmark.ts.

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
  // Older servers omit it: treated as installed.
  package_installed?: boolean
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

/** A translation arm's options: the cases of a title run together, in order, the way a title is translated. A
 * field left out takes what a title's Translate starts with (BenchmarkOptions.translate_choices). */
export interface BenchmarkTranslateOptions {
  batch_size?: number
  context_window?: number
  context_window_ahead?: number
  reflect?: boolean
  style_preset?: string
  /** A series whose glossary, learned style and character hints the arm translates with. */
  series_id?: number
  memory_examples?: boolean
  temperature?: number
}

export interface BenchmarkTranslateChoices {
  style_presets: { key: string; label: string }[]
  series: { id: number; name: string }[]
  /** Everything but the batch size, which follows the engine and model (batch_size_defaults). */
  defaults: { context_window: number; context_window_ahead: number; batch_size: number; reflect: boolean; style_preset: string; memory_examples: boolean }
  /** The engines that take a temperature. */
  temperature_engines: string[]
  /** {engine: {model or '' for the engine's default: lines per request}}. */
  batch_size_defaults: Record<string, Record<string, number>>
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
interface BenchmarkTimingVerdict {
  state: 'baseline' | 'repeat' | 'untimed' | 'no_margin' | 'no_gain' | 'cer_worse' | 'gain'
  text: string
  margin_within: number | null
  margin_score: number | null
  gain?: number | null
  score_change?: number | null
}

interface BenchmarkBackendOption {
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
  translate_choices: BenchmarkTranslateChoices
  ocr_backends: string[]
  /** Vision engines that can also read a picture in the OCR stage (paid ones need a key and count toward the cap). */
  vision_engines: string[]
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
  /** Made from a title's video: pictures of the subtitle band, read by a vision arm. */
  hard_sub?: boolean
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

/** The engine that scores a translation run's outputs. allow_same_model must be true to judge with a tested model. */
interface BenchmarkJudgeConfig {
  engine: string
  model?: string
  allow_same_model?: boolean
}

export interface BenchmarkRunRequest {
  stage: BenchmarkStage
  configs: BenchmarkRequestConfig[]
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

interface BenchmarkJudgeEstimate {
  engine: string
  model: string | null
  estimated_cost_usd: number
  cap_applies: boolean
  same_as_tested: string[]
  warning: string | null
}

interface BenchmarkConfigEstimate {
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
  picks?: BenchmarkPickTotals | null
  script_counts?: ScriptCounts | null
  timing?: BenchmarkTimingSummary
  timing_verdict?: BenchmarkTimingVerdict | null
}

/** How often the owner picked this output over another run's (blind A/B). */
export interface BenchmarkPickTotals {
  won: number
  lost: number
  tie: number
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
  picks?: BenchmarkPickTotals | null
  script_counts?: ScriptCounts | null
  ref_version?: number
  timing?: BenchmarkCaseTiming | null
  /** What the recogniser or vision arm read, on an end-to-end or read-then-translate result. */
  recognised_text?: string | null
  /** A read-then-translate result: how well the picture was read, beside the translation's own score. */
  read_score?: number | null
}

interface BenchmarkArenaRow {
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
export interface BenchmarkPairNext {
  total: number
  remaining: number
  done: boolean
  case_id?: number | null
  label?: string | null
  source_text?: string | null
  reference_text?: string | null
  left_text?: string | null
  right_text?: string | null
  ticket?: string | null
}

export type BenchmarkPickChoice = 'left' | 'right' | 'tie'

export interface BenchmarkPickSummary {
  run_a: number
  run_b: number
  total: number
  picked: number
  a_wins: number
  b_wins: number
  ties: number
  judge_agrees: number
  judge_disagrees: number
  judge_undecided: number
}

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
