// Mirrors api/schemas.py for the Workspace Source/Transcribe stage:
// MediaStatus, MediaUploadResult, UploadAndTranscribeResult, TranscribeConfig(Update),
// TranscribeRunRequest/Result, DiarizationRunResult, NovelAttach*, NovelStatus.

export interface MediaStatus {
  drama_id: number
  has_audio: boolean
  has_source_video: boolean
  upload_max_mb: number
}

export interface MediaUploadResult {
  name: string
  size: number
  kind: string
  // Set for a video: the background audio-extraction job (B-09).
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
  beam_size: number
  min_silence_ms: number
  vad_threshold: number
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
    | 'has_video_source'
  >
>

export interface TranscribeRunRequest {
  source_language?: string | null
  chinese_script?: string | null
  transcript_text?: string | null
  run_diarize?: boolean
  expected_speakers?: number | null
  // Step 105: a speaker-count range for "Detect speakers after transcribing".
  min_speakers?: number | null
  max_speakers?: number | null
  initial_prompt?: string
  extra_names?: string
  tesseract_cmd?: string | null
}

// GET /api/diarization/dramas/{id}/config (api/schemas.py DiarizationConfig).
export interface DiarizationConfig {
  drama_id: number
  hf_token_configured: boolean
  expected_speakers: number | null // the last run's count (D03)
  min_speakers: number | null
  max_speakers: number | null
  last_device: string | null
  audio_available: boolean
  manual_speaker_count?: number // lines whose speaker was corrected by hand (D06)
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

// Step 115b: mirrors api/schemas.py LncrawlStatus / LncrawlImportRequest.
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

// Mirrors api/schemas.py MediaAnalysis / AutofillRequest / AutofillSuggestion (Slice 37).
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

// api/schemas.py SourceConfig / SourceConfigUpdate (Source-stage config).
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

// api/schemas.py RetranscribeLineRequest / RetranscribeLineResult (parity audit B1, R23).
export interface RetranscribeLineRequest {
  initial_prompt?: string
  extra_names?: string
}

export interface RetranscribeLineResult {
  job_id: string
  drama_id: number
  line_id: number
}

// api/schemas.py RetranscribeResult: the finished proposal, raw (GET .../retranscribe).
export interface RetranscribeResult {
  job_id: string
  line_id: number
  status: string
  proposed_zh: string
  base_zh: string
}

// api/schemas.py RetranscribeApplyRequest / RetranscribeApplyResult: "Use this"
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

// GET /api/workflow/dramas/{id}/progress (api/schemas.py WorkflowProgress).
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
