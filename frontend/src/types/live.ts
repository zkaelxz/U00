// Hand-written mirrors of api/schemas/transcribe.py (Live* models).

export type LiveStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

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
  /** Instead of a title: the series' glossary and the packs of its newest title with a pack choice. */
  series_id?: number
}

export interface LiveOllamaCheck {
  ok: boolean
  model: string
  message: string | null
}

export interface LiveSessionStarted {
  session_id: string
}

/** pending until the engine answers; failed, cancelled and skipped (the translation queue was full) keep the transcript. */
export type LiveTranslation = 'pending' | 'done' | 'failed' | 'cancelled' | 'skipped'

export interface LiveCue {
  /** Stable for the session: the cue's place in the list, which only grows. */
  id: number
  start: number
  end: number
  text: string
  translated: string
  translation: LiveTranslation
}

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
  cues: LiveCue[]
  next_index: number
}

export interface LiveSessionSummary {
  session_id: string
  status: LiveStatus | string
  engine: string | null
  model?: string | null
  cue_count: number
}

export interface LiveSessionStopped {
  session_id: string
  stopping: boolean
  /** True: the session captions what it already captured, then ends; Stop again ends it at once. */
  finishing?: boolean
}

// Followed channels (api/routers/live_follow_routes.py). Never a URL:
// `channel` is the short label ("@name", "channel/UC...").
export type LiveFollowMode = 'notify' | 'auto'
export type LiveFollowState = '' | 'live' | 'upcoming' | 'not_live' | 'error'

export interface LiveFollowOptions {
  source_language: string
  whisper_size: string
  engine: string | null
  model: string | null
  max_minutes: number
  use_gpu: boolean
  glossary_drama_id: number | null
  series_id: number | null
}

export interface LiveFollow {
  id: number
  channel: string
  channel_name: string
  mode: LiveFollowMode
  /** Checked automatically every hour; off (the default) means manual checks only. */
  auto_check: boolean
  options: LiveFollowOptions
  poll_minutes: number
  /** Unix seconds. */
  last_checked_at: number | null
  state: LiveFollowState
  /** Fixed plain words from the server. */
  last_status: string
  /** Unix seconds (UTC) of a scheduled stream's start. */
  starts_at: number | null
  live_now: boolean
  /** The live or scheduled stream's title, as the site gives it. */
  stream_title: string
  /** A check of this channel is queued or running. */
  checking: boolean
  mine: boolean
  created_at: number
}

export interface LiveFollowList {
  items: LiveFollow[]
  /** The PC owner only: may turn on "Start Live automatically" for their own channels. */
  can_auto_start: boolean
  max_per_member: number
  max_total: number
}

export interface LiveFollowAdd {
  channel: string
  mode?: LiveFollowMode
  auto_check?: boolean
  options?: LiveFollowOptions
}

export interface LiveFollowUpdate {
  mode?: LiveFollowMode
  auto_check?: boolean
  options?: LiveFollowOptions
}
