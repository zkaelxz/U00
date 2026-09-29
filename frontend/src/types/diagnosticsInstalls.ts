// Mirrors api/diagnostics_install_schemas.py (Deno install).

export interface DiagnosticsJobState {
  status: string | null
  progress: number
  message: string
  error: string | null
}

export interface DiagnosticsJobStarted {
  job_id: string
  started: boolean
}

export interface DiagnosticsDenoResult {
  ok: boolean
  on_path: boolean
  needs_restart: boolean
  message: string
  output_tail: string[]
}

export interface DiagnosticsDenoStatus {
  runtime_found: boolean
  runtime_name: string | null
  deno_on_path: boolean
  deno_installed: boolean
  can_install: boolean
  install_method: 'winget' | 'download' | string
  job_id: string
  job: DiagnosticsJobState | null
  last_result: DiagnosticsDenoResult | null
}
