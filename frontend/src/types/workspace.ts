// Mirrors api/schemas/library.py (Media*), transcribe.py (Transcribe*, Diarization*) and
// reader.py (Novel*) for the Workspace Source/Transcribe stage:
// MediaStatus, MediaUploadResult, UploadAndTranscribeResult, TranscribeConfig(Update),
// TranscribeRunRequest/Result, DiarizationRunResult, NovelAttach*, NovelStatus.

export interface MediaStatus {
  drama_id: number
  has_audio: boolean
  has_source_video: boolean
  reads_burned_in_subtitles?: boolean
  upload_max_mb: number
  // Superseded originals and failed uploads kept in the title's folder (numbers only).
  kept_media_files: number
  kept_media_bytes: number
}

export interface MediaUploadResult {
  name: string | null
  size: number
  kind: string
  // Set for a video: the background audio-extraction job.
  job_id?: string | null
}

export interface UploadAndTranscribeResult {
  upload: MediaUploadResult
  job_id: string
}

export interface TranscribeConfig {
  drama_id: number
  transcript_mode: string
  has_audio_pipeline: boolean
  audio_available: boolean
  alignment_method: string
  asr_backend_choice: string
  whisper_size: string
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
  beam_size: number
  min_silence_ms: number
  vad_threshold: number
  // Shortest silence between words at which a long line may be cut.
  min_pause_sec: number
  // Seconds of silence inside a segment that make Whisper skip it; 0 = off.
  hallucination_silence_sec: number
  separate_vocals_first: boolean
  separation_backend: string
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  use_groq: boolean
  has_video_source: boolean
  hardsub_ocr_backend: string
  hardsub_interval_sec: number
  // Whisper prompt from the series glossary and raw-novel excerpt; a run with an empty prompt uses it.
  auto_initial_prompt: string
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
    | 'has_video_source'
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
  expected_speakers: number | null // the last run's count (D03)
  min_speakers: number | null
  max_speakers: number | null
  last_device: string | null
  audio_available: boolean
  manual_speaker_count?: number // lines whose speaker was corrected by hand (D06)
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

export interface NovelAttachResult {
  char_count: number
  // EPUB attach only: chapters found and the range used (1-based, inclusive).
  epub_chapters?: number
  chapter_from?: number
  chapter_to?: number
}

// POST /api/metadata/dramas/{id}/romanize-credits
export interface RomanizeCreditsResult {
  drama_id: number
  romanized: Record<string, string>
  updated: boolean
}

// POST /api/dramas/{id}/cover
export interface CoverArtResult {
  drama_id: number
  has_cover_art: boolean
  format: string
  width: number
  height: number
  size_bytes: number
}

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
export interface LncrawlStatus {
  installed: boolean
  path_configured: boolean
}

export type LncrawlChapters = 'all' | 'first' | 'last'

export interface LncrawlImportRequest {
  url: string
  chapters: LncrawlChapters
  count?: number
  mode: NovelMode
}

export interface NovelStatus {
  drama_id: number
  has_novel_text: boolean
  char_count: number
  chapters: number
  ocr_running: boolean
}

// Mirrors api/schemas/library.py MediaAnalysis / AutofillRequest / AutofillSuggestion.
export interface MediaAnalysis {
  drama_id: number
  duration_seconds: number
  has_video: boolean
  has_audio: boolean
  audio_track_count: number
  sample_rate: number | null
  // Parity P05 (B1); optional so an older server still type-checks.
  width?: number | null
  height?: number | null
  fps?: number | null
  subtitle_tracks?: MediaSubtitleTrack[]
  suggested_pipeline?: string[] // advisory steps, plain words
  content_type_guess?: string | null // a media type value
  content_type_reason?: string | null
}

export interface MediaSubtitleTrack {
  index: number | null
  codec: string
  language: string | null
}

export interface AutofillRequest {
  url?: string
  page_text?: string
}

export interface AutofillSuggestion {
  drama_id: number
  suggestion: Record<string, string>
  found: boolean
}

// api/schemas/transcribe.py SourceConfig / SourceConfigUpdate (Source-stage config).
export interface SourceConfig {
  drama_id: number
  source_language: string
  chinese_script: string
  content_mode: string
  has_audio_pipeline: boolean
  audio_available: boolean
  has_video_source: boolean
  transcript_mode: string
  transcript_mode_options: string[]
  has_raw_novel_context: boolean
}

export type SourceConfigUpdate = Partial<
  Pick<SourceConfig, 'source_language' | 'chinese_script' | 'content_mode' | 'transcript_mode'>
>

// api/schemas/transcribe.py RetranscribeLineRequest / RetranscribeLineResult.
export interface RetranscribeLineRequest {
  initial_prompt?: string
  extra_names?: string
}

export interface RetranscribeLineResult {
  job_id: string
  drama_id: number
  line_id: number
}

// api/schemas/transcribe.py RetranscribeResult: the finished proposal, raw (GET .../retranscribe).
export interface RetranscribeResult {
  job_id: string
  line_id: number
  status: string
  proposed_zh: string
  base_zh: string
}

// api/schemas/transcribe.py RetranscribeApplyRequest / RetranscribeApplyResult: "Use this"
// for exactly the proposal shown (expected_zh = base_zh, expected_proposed = proposed_zh).
export interface RetranscribeApplyRequest {
  job_id: string
  expected_zh: string
  expected_proposed: string
}

export interface RetranscribeApplyResult {
  drama_id: number
  line_id: number
  zh: string
}

// GET /api/workflow/dramas/{id}/progress (api/schemas/library.py WorkflowProgress).
type WorkflowStageStateName = 'done' | 'current' | 'pending' | 'optional' | 'blocked'

export interface WorkflowStageState {
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

export interface CompareBackendOption {
  id: string
  label: string
  available: boolean
  reason: string | null
}

export interface CompareOptions {
  has_audio: boolean
  no_audio_reason: string | null
  max_lines: number
  saved_whisper_size: string
  saved_asr_backend: string
  saved_alignment_method: string
  whisper_sizes: string[]
  backends: CompareBackendOption[]
  translation_engine: string
}

export interface CompareTranslateFields {
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

export interface CompareRunResult {
  job_id: string
  drama_id: number
  line_count: number
}

export interface CompareProposal {
  line_id: number
  number: number
  start: number
  end: number
  base_zh: string
  base_en: string
  candidate_zh: string
  current_en: string
  candidate_en: string
  translated: boolean
}

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

export interface CompareApplyItem {
  line_id: number
  expected_base_zh: string
  expected_candidate_zh: string
  use_english?: boolean
  expected_candidate_en?: string
}

export interface CompareApplyRequest {
  job_id: string
  items: CompareApplyItem[]
}

export interface CompareApplyResult {
  applied: number[]
  skipped: number[]
}

// api/schemas/transcribe.py Retime* (Review: Re-time with the Qwen3 aligner).
export interface RetimeRunRequest {
  line_ids: number[]
}

export interface RetimeProposal {
  line_id: number
  number: number
  base_zh: string
  start: number
  end: number
  new_start: number
  new_end: number
  uncertain: boolean
}

export interface RetimeResult {
  job_id: string
  proposals: RetimeProposal[]
  line_count: number
  partial: boolean
  device: string | null
  device_notice: string | null
  errors: string[]
}

export interface RetimeApplyItem {
  line_id: number
  expected_new_start: number
  expected_new_end: number
}

export interface RetimeApplyRequest {
  job_id: string
  items: RetimeApplyItem[]
}

export interface RetimeApplyResult extends CompareApplyResult {
  overlapping: number[]
}
