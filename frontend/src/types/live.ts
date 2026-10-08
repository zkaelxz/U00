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
}

export interface LiveSessionStarted {
  session_id: string
}

export interface LiveCue {
  start: number
  end: number
  text: string
  translated: string
}

export interface LiveSessionStatus {
  session_id: string
  status: LiveStatus | string
  message: string
  engine?: string | null
  model?: string | null
  progress: number
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
