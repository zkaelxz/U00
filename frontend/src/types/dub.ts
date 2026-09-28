// Mirrors api/schemas.py DubConfig / DubPacing / DubRunRequest and
// NarrationConfig / NarrationRunRequest. No paths or secrets exist here.

export interface DubTtsEngine {
  key: string
  label: string
  requires_internet: boolean
}

export interface DubDefaults {
  max_speedup: number
  max_slowdown: number
  speedup_range: number[]
  slowdown_range: number[]
}

export interface DubConfig {
  drama_id: number
  content_mode: string | null
  is_narration: boolean
  narration_language: string
  narration_language_options: string[]
  source_language: string
  tts_engines: DubTtsEngine[]
  defaults: DubDefaults | null
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
