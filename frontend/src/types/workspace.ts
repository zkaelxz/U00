// Mirrors api/schemas/library.py (Media*), transcribe.py (Transcribe*, Diarization*) and
// reader.py (Novel*) for the Workspace Source/Transcribe stage:
// MediaStatus, MediaUploadResult, UploadAndTranscribeResult, TranscribeConfig(Update),
// TranscribeRunRequest/Result, DiarizationRunResult, NovelAttach*, NovelStatus.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type MediaStatus = Schemas['MediaStatus']

export type MediaUploadResult = Schemas['MediaUploadResult']

export type UploadAndTranscribeResult = Schemas['UploadAndTranscribeResult']

// The Transcribe form's run choices saved on a title (the transcript text is never kept).
export type TranscribeRunStart = Schemas['TranscribeRunStart']

export interface TranscribeConfig {
  drama_id: number
  transcript_mode: string
  has_audio_pipeline: boolean
  audio_available: boolean
  alignment_method: string
  asr_backend_choice: string
  // Set when the saved backend was removed and the default is shown instead.
  asr_backend_notice: string | null
  whisper_size: string
  // Set when the saved model is not recommended for the title's language and the default is shown instead.
  whisper_size_notice: string | null
  // {model: {language: reason}} for models not recommended for a language.
  whisper_exclusions: Record<string, Record<string, string>>
  // The sizes the picker lists (server: whisper_models.selectable_whisper_sizes); absent from an older server.
  selectable_whisper_sizes?: string[]
  whisper_model_cached: boolean
  // Audio seconds per second of work on the last finished run of this model and device; null if none yet.
  measured_speed: number | null
  // How many recent runs that speed is the median of.
  measured_speed_runs?: number
  // Median seconds per stage (separate, load, decode_vad, transcribe, align) over those runs.
  measured_stage_seconds?: Record<string, number>
  // Audio seconds per second of speaker detection on this device; null until enough runs.
  measured_diarize_speed?: number | null
  measured_diarize_runs?: number
  // False when faster-whisper isn't installed (transcription can't run).
  whisper_installed: boolean
  // CUDA runtime DLLs Whisper needs on the GPU; "unknown" off Windows or without a CUDA device.
  gpu_runtime: { status: 'ok' | 'missing' | 'unknown'; reason: string | null }
  beam_size: number
  min_silence_ms: number
  vad_threshold: number
  // "normal" or "sensitive"; the threshold a run uses (the preset lowers an untouched one).
  sensitivity_preset: string
  effective_vad_threshold: number
  // Shortest silence between words at which a long line may be cut.
  min_pause_sec: number
  // Seconds of silence inside a segment that make Whisper skip it; 0 = off.
  hallucination_silence_sec: number
  separate_vocals_first: boolean
  separation_backend: string
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  // Whisper's no-repeat and repetition-penalty decoding; off by default.
  whisper_repeat_guard: boolean
  // Cut lines at sentence ends and word pauses instead of speech-detector pauses.
  split_by_sentences: boolean
  // Levels the audio, uses the sensitive detector and relaxes Whisper's skip rule; off by default.
  difficult_audio: boolean
  // Qwen3-ASR gets this title's character names and glossary terms as a spelling hint; off by default.
  vocabulary_hint: boolean
  use_groq: boolean
  has_video_source: boolean
  hardsub_ocr_backend: string
  hardsub_interval_sec: number
  // Whisper prompt from the series glossary and raw-novel excerpt; a run with an empty prompt uses it.
  auto_initial_prompt: string
  // The language, Chinese script and run choices saved on the title; absent from an older server.
  source_language?: string
  chinese_script?: string
  run_start?: TranscribeRunStart
}

// Every field optional: only what is sent is validated and written.
export type TranscribeConfigUpdate = Partial<
  Omit<
    TranscribeConfig,
    | 'drama_id'
    | 'transcript_mode'
    | 'has_audio_pipeline'
    | 'audio_available'
    | 'whisper_model_cached'
    | 'measured_speed'
    | 'measured_speed_runs'
    | 'measured_stage_seconds'
    | 'measured_diarize_speed'
    | 'measured_diarize_runs'
    | 'whisper_installed'
    | 'gpu_runtime'
    | 'has_video_source'
    | 'effective_vad_threshold'
  >
>

export interface TranscribeRunRequest {
  source_language?: string | null
  chinese_script?: string | null
  transcript_text?: string | null
  run_diarize?: boolean
  expected_speakers?: number | null
  // A speaker-count range for "Detect speakers after transcribing".
  min_speakers?: number | null
  max_speakers?: number | null
  initial_prompt?: string
  extra_names?: string
  tesseract_cmd?: string | null
}

// GET /api/diarization/dramas/{id}/config (api/schemas/transcribe.py DiarizationConfig).
export interface DiarizationConfig {
  drama_id: number
  hf_token_configured: boolean
  // Plain text of what stops speaker detection (no token, no pyannote); null when it can run.
  speaker_notice?: string | null
  expected_speakers: number | null // the last run's count
  min_speakers: number | null
  max_speakers: number | null
  last_device: string | null
  audio_available: boolean
  manual_speaker_count?: number // lines whose speaker was corrected by hand
  speaker_summary?: SpeakerTimeSummary | null // null: no saved detection
}

export interface SpeakerTimeSummary {
  speakers: { label: string; seconds: number; percent: number; turns: number }[]
  total_speech_seconds: number
  uncovered_seconds: number | null
}

export interface JobStarted {
  job_id: string
}

export type NovelAttachResult = Schemas['NovelAttachResult']

// POST /api/metadata/dramas/{id}/romanize-credits
export type RomanizeCreditsResult = Schemas['RomanizeCreditsResult']

// POST /api/dramas/{id}/cover
export type CoverArtResult = Schemas['CoverArtResult']

// GET /api/discover/platforms (known_sites.KNOWN_SITES)
export interface KnownPlatform {
  name: string
  url: string
  region?: string
  language?: string
  content_types?: string[]
  notes?: string
}

export type NovelMode = 'replace' | 'append'

// Mirrors api/schemas/sources.py LncrawlStatus / LncrawlImportRequest.
export type LncrawlStatus = Schemas['LncrawlStatus']

export type LncrawlChapters = 'all' | 'first' | 'last'

export type LncrawlImportRequest = Schemas['LncrawlImportRequest']

export type NovelStatus = Schemas['NovelStatus']

// Mirrors api/schemas/library.py MediaAnalysis / AutofillRequest / AutofillSuggestion.
export interface MediaAnalysis {
  drama_id: number
  duration_seconds: number
  has_video: boolean
  has_audio: boolean
  audio_track_count: number
  sample_rate: number | null
  // Parity P05; optional so an older server still type-checks.
  width?: number | null
  height?: number | null
  fps?: number | null
  subtitle_tracks?: MediaSubtitleTrack[]
  suggested_pipeline?: string[] // advisory steps, plain words
  content_type_guess?: string | null // a media type value
  content_type_reason?: string | null
}

interface MediaSubtitleTrack {
  index: number | null
  codec: string
  language: string | null
}

export type AutofillRequest = Schemas['AutofillRequest']

export type AutofillSuggestion = Schemas['AutofillSuggestion']

// api/schemas/transcribe.py SourceConfig / SourceConfigUpdate (Source-stage config).
export type SourceConfig = Schemas['SourceConfig']

export type SourceConfigUpdate = Partial<
  Pick<SourceConfig, 'source_language' | 'chinese_script' | 'content_mode' | 'transcript_mode'>
>

// api/schemas/transcribe.py RetranscribeLineRequest / RetranscribeLineResult.
export interface RetranscribeLineRequest {
  initial_prompt?: string
  extra_names?: string
}

export type RetranscribeLineResult = Schemas['RetranscribeLineResult']

// api/schemas/transcribe.py RetranscribeResult: the finished proposal, raw (GET .../retranscribe).
export type RetranscribeResult = Schemas['RetranscribeResult']

// api/schemas/transcribe.py RetranscribeApplyRequest / RetranscribeApplyResult: "Use this"
// for exactly the proposal shown (expected_zh = base_zh, expected_proposed = proposed_zh).
export type RetranscribeApplyRequest = Schemas['RetranscribeApplyRequest']

export type RetranscribeApplyResult = Schemas['RetranscribeApplyResult']

// api/schemas/retranscribe_lines.py: Review's "Re-transcribe selected" (several lines, one job).
export interface RetranscribeManyRequest {
  line_ids: number[]
  initial_prompt?: string
  extra_names?: string
}

export type RetranscribeManyStarted = Schemas['RetranscribeManyStarted']

// Raw line text (the proposal is only readable here, never in the job record).
// had_english: applying it keeps that English and flags the line source_changed.
export type RetranscribeManyProposal = Schemas['RetranscribeManyProposal']

// reason: "empty" | "audio_slice" | "line_gone"
export type RetranscribeManyFailure = Schemas['RetranscribeManyFailure']

export type RetranscribeManyResult = Schemas['RetranscribeManyResult']

export type RetranscribeManyApplyItem = Schemas['RetranscribeManyApplyItem']

export type RetranscribeManyApplyResult = Schemas['RetranscribeManyApplyResult']

// A stretch no subtitle line covers. speech: true when the speech coverage
// check found speech there, null when it has not run (never false).
export interface TranscribeGap {
  start: number
  end: number
  seconds: number
  pieces: number
  part: number
  parts: number
  after_line_id: number | null
  before_line_id: number | null
  speech: boolean | null
}

export interface TranscribeGaps {
  gaps: TranscribeGap[]
  speech_checked: boolean
}

export type GapAddLinesRequest = Schemas['GapAddLinesRequest']

export type GapAddLinesResult = Schemas['GapAddLinesResult']

export type TalkOverAddResult = Schemas['TalkOverAddResult']

// GET /api/workflow/dramas/{id}/progress (api/schemas/library.py WorkflowProgress).
type WorkflowStageStateName = 'done' | 'current' | 'pending' | 'optional' | 'blocked'

interface WorkflowStageState {
  key: string
  state: WorkflowStageStateName
}

export interface WorkflowProgress {
  drama_id: number
  stage_index: number
  stage: string
  line_count: number
  untranslated_count: number
  flagged_count: number
  has_audio: boolean
  has_dub_track: boolean
  has_narration_track?: boolean
  exported: boolean
  stages: WorkflowStageState[]
}

// api/schemas/transcribe.py Compare* (Review: Compare transcription).
export type CompareSelection =
  | { kind: 'line_ids'; line_ids: number[] }
  | { kind: 'range'; from_number: number; to_number: number }
  | { kind: 'flagged' }
  | { kind: 'speaker'; speaker: string }
  | { kind: 'time'; start_seconds: number; end_seconds: number }

export type CompareOptions = Schemas['CompareOptions']

interface CompareTranslateFields {
  translate?: boolean
  retranslate_current?: boolean
  engine?: string | null
  model?: string | null
  gemini_free_tier?: boolean | null
  job_cost_cap_usd?: number | null
}

export interface CompareEstimateRequest extends CompareTranslateFields {
  selection: CompareSelection
}

export interface CompareEstimate {
  line_count: number
  max_lines: number
  translate: boolean
  estimated_usd: number | null
  free: boolean
  cap_applies: boolean
  effective_cap_usd: number | null
  monthly_refusal: boolean
  estimate_above_cap: boolean
}

export interface CompareRunRequest extends CompareTranslateFields {
  selection: CompareSelection
  whisper_size?: string | null
  asr_backend?: string | null
  initial_prompt?: string
  extra_names?: string
}

export type CompareRunResult = Schemas['CompareRunResult']

export type CompareProposal = Schemas['CompareProposal']

export interface CompareResult {
  job_id: string
  proposals: CompareProposal[]
  line_count: number
  asr_backend: string | null
  whisper_size: string | null
  translated: boolean
  partial: boolean
  cap_reached: boolean
  errors: string[]
}

export type CompareApplyRequest = Schemas['CompareApplyRequest']

export type CompareApplyResult = Schemas['CompareApplyResult']

// api/schemas/transcribe.py Retime* (Review: Re-time with the Qwen3 aligner).
export type RetimeRunRequest = Schemas['RetimeRunRequest']

export type RetimeProposal = Schemas['RetimeProposal']

export type RetimeResult = Schemas['RetimeResult']

export type RetimeApplyRequest = Schemas['RetimeApplyRequest']

export interface RetimeApplyResult extends CompareApplyResult {
  overlapping: number[]
}

export interface AsrAdviceRequest {
  language: string
  media_type: string
  asr_backend: string
  whisper_size: string
  separate_vocals_first: boolean
  whisper_fast_mode: boolean
  use_groq: boolean
  has_transcript: boolean
  alignment_method: string
  use_gpu: boolean | null
  /** False for a run that already finished: what is installed now says nothing about it. */
  check_environment: boolean
}

export type AsrAdviceNote = Schemas['AsrAdviceNote']
