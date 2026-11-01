
export interface ListenTtsEngine {
  key: string
  label: string
  // Fixed-text reason this engine can't run here; null or absent when it can.
  unavailable_reason?: string | null
}

export interface ListenConfig {
  drama_id: number
  narration_language: string
  narration_language_options: string[]
  tts_engines: ListenTtsEngine[]
  default_engine: string
  // Why nothing can be narrated whatever engine is picked; null when the engine decides.
  blocker: string | null
  gpu_required: boolean
  speakable_line_count: number
  track_available: boolean
}

export interface ListenRunRequest {
  tts_engine: string
  narration_language?: string
}
