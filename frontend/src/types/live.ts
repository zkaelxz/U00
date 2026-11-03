// Hand-written mirrors of api/schemas/transcribe.py (Live* models).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

type LiveStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface LiveSessionStart {
  url: string
  source_language: string
  whisper_size: string
  segment_seconds: number
  overlap_seconds: number
  engine: string | null
  model?: string | null
  max_minutes: number
  use_gpu: boolean
  reply_without_thinking: boolean
  /** Also record a delayed copy of the picture for the page's own player. */
  delayed_player?: boolean
  /** With the delayed player: the tallest picture to record, in pixels. */
  player_height?: number
  /** A title whose glossary and language packs the translation may use (read-only). */
  glossary_drama_id?: number
  /** YouTube links only: also read the stream's live chat and translate it. */
  read_chat?: boolean
  /** Instead of a title: the series' glossary and the packs of its newest title with a pack choice. */
  series_id?: number
}

/** GET /api/live/options: what the form offers and starts on (the values Start checks). */
export interface LiveFormOptions {
  languages: { code: string; label: string }[]
  whisper_sizes: string[]
  segment_range: [number, number]
  overlap_range: [number, number]
  max_minutes_range: [number, number]
  max_url_len: number
  player_heights: number[]
  default_engine: string
  default_model: string | null
  thinking_switch_engines: string[]
  defaults: {
    source_language: string
    whisper_size: string
    segment_seconds: number
    overlap_seconds: number
    max_minutes: number
    player_height: number
  }
}

export type LiveOllamaCheck = Schemas['LiveOllamaCheck']

export type LiveSessionStarted = Schemas['LiveSessionStarted']

export type LiveCue = Schemas['LiveCue']

export interface LiveSessionStatus {
  session_id: string
  status: LiveStatus | string
  message: string
  /** Why the session ended itself (monthly cap reached, repeated translation failures). */
  stop_reason?: string | null
  engine?: string | null
  model?: string | null
  progress: number
  /** Skipped chunks and catching up, newest last. */
  notes?: string[]
  segment_seconds?: number
  /** The stream's language as the session was started with it. */
  source_language?: string | null
  /** Seconds from the first word of the newest captioned chunk to its translation. */
  lag_seconds?: number
  /** Rolling median of one chunk's wait, Whisper and translation seconds. */
  processing_seconds?: number
  /** Lines shown untranslated because the translator fell behind the stream. */
  translations_skipped?: number
  translate_queue?: number
  chunks_measured?: number
  /** Whether this session records a delayed copy for the page's own player. */
  delayed_player?: boolean
  /** True once the delayed player's recording has something to play. */
  player_ready?: boolean
  /** Whether this session reads the stream's chat (GET .../chat has the messages). */
  chat?: boolean
  cues: LiveCue[]
  next_index: number
}

export type LiveSessionSummary = Schemas['LiveSessionSummary']

export type LiveSessionStopped = Schemas['LiveSessionStopped']

// Followed channels (api/routers/live_follow_routes.py). Never a URL:
// `channel` is the short label ("@name", "channel/UC...").
export type LiveFollowMode = 'notify' | 'auto'
export type LiveFollowOptions = Schemas['LiveFollowOptions']

export type LiveFollow = Schemas['LiveFollow']

export type LiveFollowList = Schemas['LiveFollowList']

export type LiveFollowAdd = Schemas['LiveFollowAdd']

export type LiveFollowUpdate = Schemas['LiveFollowUpdate']

export type LiveChatMessage = Schemas['LiveChatMessage']

/** GET /api/live/sessions/{id}/chat?after=: messages with an id above `after`. */
export type LiveChatPage = Schemas['LiveChatPage']
