// Mirrors api/schemas/voice.py DubConfig / DubPacing / DubRunRequest and
// NarrationConfig / NarrationRunRequest. No paths or secrets exist here.

export interface DubTtsEngine {
  key: string
  label: string
  unavailable_reason?: string | null
}

export interface DubDefaults {
  max_speedup: number
  max_slowdown: number
  speedup_range: number[]
  slowdown_range: number[]
}

// One speaker as the Generate button resolves it. clone_warning: why the
// speaker won't be cloned as set up (e.g. a clone engine with no clip or
// voice design falls back to the engine picked in Dub, or its stored engine
// was removed).
export interface DubSpeaker {
  speaker_label: string
  character_name: string | null
  engine: string
  has_clone_ref: boolean
  clone_warning?: string | null
}

export interface DubConfig {
  drama_id: number
  content_mode: string | null
  is_narration: boolean
  narration_language: string
  narration_language_options: string[]
  source_language: string
  tts_engines: DubTtsEngine[]
  default_engine: string
  // Why nothing can be generated whatever engine is picked (no engine
  // installed, or a character stored with a removed engine); null otherwise.
  blocker?: string | null
  defaults: DubDefaults | null
  speakers?: DubSpeaker[]
  gpu_required: boolean
  speakable_line_count: number
  track_available: boolean
  gpt_sovits_configured: boolean
  can_keep_background: boolean
}

export interface DubPacingLine {
  idx: number
  status: string
  factor: number
  clip_ms: number | null
  window_ms: number | null
}

export interface DubPacing {
  available: boolean
  counts: Record<string, number>
  lines: DubPacingLine[]
}

export interface DubRunRequest {
  tts_engine: string
  max_speedup?: number
  max_slowdown?: number
  narration_language?: string
  keep_background: boolean
}

export interface NarrationEngineOption {
  key: string
  key_configured: boolean
}

export interface NarrationConfig {
  drama_id: number
  is_narration: boolean
  has_novel_source: boolean
  engines: NarrationEngineOption[]
  default_engine: string
  max_chunk_chars: number
  existing_line_count: number
  replaces_existing_lines: boolean
  job_running: boolean
}

export interface NarrationRunRequest {
  engine?: string
  model?: string
}
