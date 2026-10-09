// Speech coverage check (api/routers/transcribe_routes.py): speech in the
// stored audio that no subtitle line covers. GET answers status "idle" when
// no check is held in this app session.
export type RawStatus = 'lost_after' | 'none' | 'unknown'

export interface SpeechCoverageGap {
  start: number
  end: number
  seconds: number
  speech_seconds: number
  raw_status: RawStatus
  raw_text: string
  after_line_id: number | null
  before_line_id: number | null
}

export interface SpeechCoverageReport {
  audio_seconds: number | null
  speech_seconds: number | null
  covered_seconds: number | null
  covered_percent: number | null
  vad_threshold: number | null
  min_gap_seconds: number | null
  raw_available: boolean
  gaps_total: number
  gaps: SpeechCoverageGap[]
  failed_reason: string | null
  detail: string | null
}

export interface SpeechCoverageStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  result: SpeechCoverageReport | null
}
