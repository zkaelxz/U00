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

export type TranscribeConfig = Schemas['TranscribeConfig']

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

export type TranscribeRunRequest = Schemas['TranscribeRunRequest']

// GET /api/diarization/dramas/{id}/config (api/schemas/transcribe.py DiarizationConfig).
export type DiarizationConfig = Schemas['DiarizationConfig']

export type SpeakerTimeSummary = Schemas['SpeakerTimeSummary']

export interface JobStarted {
  job_id: string
}

export type NovelAttachResult = Schemas['NovelAttachResult']
export type NovelChaptersAdded = Schemas['NovelChaptersAdded']

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
export type RetranscribeLineRequest = Schemas['RetranscribeLineRequest']

export type RetranscribeLineResult = Schemas['RetranscribeLineResult']

// api/schemas/transcribe.py RetranscribeResult: the finished proposal, raw (GET .../retranscribe).
export type RetranscribeResult = Schemas['RetranscribeResult']

// api/schemas/transcribe.py RetranscribeApplyRequest / RetranscribeApplyResult: "Use this"
// for exactly the proposal shown (expected_zh = base_zh, expected_proposed = proposed_zh).
export type RetranscribeApplyRequest = Schemas['RetranscribeApplyRequest']

export type RetranscribeApplyResult = Schemas['RetranscribeApplyResult']

// api/schemas/retranscribe_lines.py: Review's "Re-transcribe selected" (several lines, one job).
export type RetranscribeManyRequest = Schemas['RetranscribeManyRequest']

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
export type TranscribeGap = Schemas['TranscribeGap']

export type TranscribeGaps = Schemas['TranscribeGaps']

export type GapAddLinesRequest = Schemas['GapAddLinesRequest']

export type GapAddLinesResult = Schemas['GapAddLinesResult']

export type TalkOverAddResult = Schemas['TalkOverAddResult']

export type WorkflowProgress = Schemas['WorkflowProgress']

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

export type CompareEstimate = Schemas['CompareEstimate']

export interface CompareRunRequest extends CompareTranslateFields {
  selection: CompareSelection
  whisper_size?: string | null
  asr_backend?: string | null
  initial_prompt?: string
  extra_names?: string
}

export type CompareRunResult = Schemas['CompareRunResult']

export type CompareProposal = Schemas['CompareProposal']

export type CompareResult = Schemas['CompareResult']

export type CompareApplyRequest = Schemas['CompareApplyRequest']

export type CompareApplyResult = Schemas['CompareApplyResult']
export type CompareFlagResult = Schemas['CompareFlagResult']

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
