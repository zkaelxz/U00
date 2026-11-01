// Mirrors api/schemas/voice.py NarrationConfig / NarrationRunRequest (the
// chunk-and-tag step of a narrated novel). No paths or secrets exist here.

interface NarrationEngineOption {
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
