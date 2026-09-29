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
  initial_prompt?: string
}

export interface JobStarted {
  job_id: string
}

export interface NovelAttachResult {
  char_count: number
}

export type NovelMode = 'replace' | 'append'

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
