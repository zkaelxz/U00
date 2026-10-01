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

// A file or folder in a model folder outside the Hugging Face cache:
// torch.hub checkpoints (TORCH_HOME) or the audio-separator models.
export type DiagnosticsModelFolder = 'torch' | 'audio_separator'

export interface DiagnosticsModelFile {
  folder: DiagnosticsModelFolder
  name: string
  size_bytes: number
}

export interface DiagnosticsModelCache {
  hf_cache: DiagnosticsHfCacheEntry[]
  hf_total_bytes: number
  piper_voices: DiagnosticsPiperVoice[]
  piper_total_bytes: number
  model_files: DiagnosticsModelFile[]
  model_files_total_bytes: number
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
  // From installed metadata; null when not installed or unreadable.
  installed_version?: string | null
  // The app's minimum (requirements files' >=) and whether the installed one is older.
  min_version?: string | null
  below_min?: boolean
  installable: boolean
  powers: string
  approx_mb: number | null
  pulls_torch: boolean
  source_url: string | null
  not_offered_reason: string | null
  warning: string | null
}

export type TaskRole = 'required' | 'recommended' | 'optional'

export interface DiagnosticsInstallTask {
  id: string
  group: string
  label: string
  help: string
  packages: string[]
  // Per package: required (the task needs it), recommended, optional.
  roles?: Record<string, TaskRole>
  installed_count: number
  required_missing?: string[]
  // Missing required + recommended: what "Install for this task" installs.
  to_install: string[]
  // Missing optional extras, installed one by one from Missing packages.
  optional_missing?: string[]
  approx_mb: number
}

export interface DiagnosticsInstallPresets {
  tasks: DiagnosticsInstallTask[]
  packages: Record<string, DiagnosticsPackageInfo>
}

// POST /api/diagnostics/package-updates/check (PyPI, explicit click only).
export type PackageUpdateStatus = 'update' | 'up_to_date' | 'held_back' | 'managed' | 'unknown'

export interface DiagnosticsPackageUpdate {
  name: string
  dist: string
  installed_version: string | null
  status: PackageUpdateStatus
  latest: string | null
  // The exact version Update installs (status "update").
  target: string | null
  reason: string | null
}

export interface DiagnosticsPackageUpdates {
  checked_at: number
  packages: Record<string, DiagnosticsPackageUpdate>
}

// GET /api/diagnostics/gpu-torch and POST /api/diagnostics/gpu-torch/setup.
export interface DiagnosticsGpuTorchNvidia {
  found: boolean
  gpu_name: string | null
  driver_version: string | null
  status: 'ok' | 'old' | 'too_old' | 'unknown'
  recommended: string | null
  minimum: string | null
}

export interface DiagnosticsTorchPackage {
  name: string
  version: string | null
  build: 'cuda' | 'cpu' | null
}

export interface DiagnosticsTorchVariant {
  variant: 'cu128' | 'cpu'
  label: string
  index_url: string
  versions: Record<string, string>
  needs_nvidia: boolean
}

export interface DiagnosticsTorchVerify {
  torch: string | null
  torchvision: string | null
  torchaudio: string | null
  cuda_build: string | null
  cuda_available: boolean | null
  device: string | null
  error: string | null
}

export type GpuTorchState = 'missing' | 'mismatched' | 'cpu_on_gpu' | 'recommended' | 'different'

export interface DiagnosticsGpuTorchStatus {
  nvidia: DiagnosticsGpuTorchNvidia
  installed: DiagnosticsTorchPackage[]
  problems: string[]
  state: GpuTorchState
  python_supported: boolean
  recommended: DiagnosticsTorchVariant
  variants: DiagnosticsTorchVariant[]
  probe: DiagnosticsTorchVerify | null
}

export interface DiagnosticsGpuTorchSetupResult extends DiagnosticsInstallResult {
  variant: string
  verify: DiagnosticsTorchVerify | null
}

export interface DiagnosticsResetResult {
  ok: boolean
  reset_at: number
}

// POST /api/diagnostics/model-cache/{hf|piper}/{name}/delete and
// /model-cache/files/{folder}/{name}/delete (PC only).
export interface DiagnosticsCacheDeleteResult {
  deleted: boolean
  name: string
}

// GET /api/diagnostics/remote-health: the last scheduled remote-access check
// (services/remote_health_service.py). States, whole days, Unix times and
// fixed messages only: never the public name, an address or a URL.
export type RemoteHealthState = 'off' | 'unknown' | 'ok' | 'warn' | 'critical'
export type RemoteCheckState = RemoteHealthState | 'not_configured'

export interface RemoteHealthCheck {
  state: RemoteCheckState
  message: string
}

/** GET /api/diagnostics/ports (PC only): numbers, flags and fixed text. */
export interface PortEntry {
  key: 'api' | 'household' | 'extension' | 'https'
  label: string
  /** null while the household listener is off. */
  port: number | null
  active: boolean
  how_to_change: string
}

export interface PortsOverview {
  ports: PortEntry[]
}

export interface RemoteHealth {
  state: RemoteHealthState
  message: string
  /** Unix seconds of the last check; null before the first one. */
  checked_at: number | null
  /** Unix seconds since the overall state has been what it is now. */
  since: number | null
  certificate: RemoteHealthCheck & { days_left: number | null }
  ddns: RemoteHealthCheck & { configured: boolean }
  listener: RemoteHealthCheck
}

// Settings > Remote access: the public-address check (PC only). The address
// is write-only: the API answers `configured` and, for Test, a state and a
// fixed message, never the address.
export interface RemoteIpCheckStatus {
  configured: boolean
}

export interface RemoteIpCheckTestResult {
  configured: boolean
  state: RemoteCheckState
  message: string
}
