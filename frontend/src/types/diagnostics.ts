// Mirrors api/schemas.py DiagnosticsOverview and its parts.

export interface DependencyStatus {
  installed: boolean
  powers: string
  tier: string
}

export interface GpuStatus {
  available: boolean
  name: string | null
  vram_used_gb: number | null
  vram_total_gb: number | null
  torch_cuda_version: string | null
  message: string | null
}

export interface ModelEngineVersion {
  name: string
  version: string | null
  url: string | null
  installed: boolean
  package: string | null
  help: string | null
}

export interface DiagnosticsOverview {
  dependencies: Record<string, DependencyStatus>
  file_completeness: {
    missing_top_level: string[]
    missing_tabs: string[]
    all_present: boolean
  }
  library_writable: boolean
  gpu: GpuStatus
  model_engine_versions: ModelEngineVersion[]
  recent_log_lines: string[]
}

// Mirrors api/schemas.py Diagnostics* (API batch 1: /api/diagnostics/...).
// Found/version/name only; never a path, a token or a key.

export interface DiagnosticsSetupChecks {
  python: { version: string | null; ok: boolean }
  // libass: built with libass (burned-in subtitles); null/absent = unknown.
  ffmpeg: { found: boolean; version: string | null; libass?: boolean | null }
  js_runtime: { found: boolean; name: string | null }
  cuda: { torch_installed: boolean; cuda_available: boolean | null }
  files: { all_present: boolean; missing_top_level: string[]; missing_tabs: string[] }
  library_writable: boolean
}

export interface DiagnosticsHfCacheEntry {
  repo_id: string
  repo_type: string
  revision: string
  size_bytes: number
}

export interface DiagnosticsPiperVoice {
  voice: string
  size_bytes: number
}

export interface DiagnosticsModelCache {
  hf_cache: DiagnosticsHfCacheEntry[]
  hf_total_bytes: number
  piper_voices: DiagnosticsPiperVoice[]
  piper_total_bytes: number
}

export interface DiagnosticsPyannoteReadiness {
  pyannote_installed: boolean
  hf_token_configured: boolean
  // null: not checked, or huggingface_hub is not installed.
  models: { model: string; accessible: boolean }[] | null
  ready: boolean
}

export interface DiagnosticsJobHistoryItem {
  job_id: string
  label: string
  status: string | null
  description: string | null
  message: string
  error: string | null
  gpu_touching: boolean
  started_at: number | null
  finished_at: number | null
  duration_seconds: number | null
}

export interface DiagnosticsLogTail {
  lines: string[]
}

export interface DiagnosticsSupportReport {
  report: string
}

export interface DiagnosticsInstallResult {
  package: string
  ok: boolean
  output_tail: string[]
}

export interface DiagnosticsResetResult {
  ok: boolean
  reset_at: number
}

// POST /api/diagnostics/model-cache/{hf|piper}/{name}/delete (PC only).
export interface DiagnosticsCacheDeleteResult {
  deleted: boolean
  name: string
}

// GET /api/diagnostics/bug-bundles: saved "What happened here?" snapshots.
export interface DiagnosticsBugBundle {
  id: number
  drama_id: number
  drama_title: string | null
  line_id: number | null
  label: string
  engine: string | null
  model: string | null
  produced_output: string
  replayed: boolean
  replay_output: string | null
  reproduced: boolean | null
  created_at: string | null
}

export interface BugBundleDeleteResult {
  bundle_id: number
  deleted: boolean
}
