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
}

export interface LiveOllamaCheck {
  ok: boolean
  model: string
  message: string | null
}

export interface LiveSessionStarted {
  session_id: string
}

/** pending until the engine answers; failed and cancelled keep the transcript. */
export type LiveTranslation = 'pending' | 'done' | 'failed' | 'cancelled'

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
  engine?: string | null
  model?: string | null
  progress: number
  /** Skipped chunks and catching up, newest last. */
  notes?: string[]
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
}
