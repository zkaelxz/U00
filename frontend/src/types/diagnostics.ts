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
  ffmpeg: { found: boolean; version: string | null }
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
  // Plain-English next step for a known failure (pip's cache unwritable).
  hint?: string | null
}

export interface DiagnosticsPackageInfo {
  name: string
  dist: string
  installed: boolean
  installable: boolean
  powers: string
  approx_mb: number | null
  pulls_torch: boolean
  source_url: string | null
  not_offered_reason: string | null
  warning: string | null
}

export interface DiagnosticsInstallTask {
  id: string
  group: string
  label: string
  help: string
  packages: string[]
  installed_count: number
  to_install: string[]
  approx_mb: number
}

export interface DiagnosticsInstallPresets {
  tasks: DiagnosticsInstallTask[]
  packages: Record<string, DiagnosticsPackageInfo>
}

export interface DiagnosticsResetResult {
  ok: boolean
  reset_at: number
}
