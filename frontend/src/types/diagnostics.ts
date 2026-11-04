// Mirrors api/schemas/system.py DiagnosticsOverview and its parts.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type DependencyStatus = Schemas['DependencyStatus']

export type GpuStatus = Schemas['GpuStatus']

export type ModelEngineVersion = Schemas['ModelEngineVersion']

export type DiagnosticsModelDownloads = Schemas['DiagnosticsModelDownloads']
export type DiagnosticsModelDownload = Schemas['DiagnosticsModelDownload']

export type DiagnosticsOverview = Schemas['DiagnosticsOverview']

// Mirrors api/schemas/diagnostics_setup.py Diagnostics* (/api/diagnostics/...).
// Found/version/name only; never a path, a token or a key.

export interface DiagnosticsSetupChecks {
  python: { version: string | null; ok: boolean }
  // libass: built with libass (burned-in subtitles); null/absent = unknown.
  ffmpeg: { found: boolean; version: string | null; libass?: boolean | null }
  js_runtime: { found: boolean; name: string | null }
  browser?: { found: boolean; name: string | null; package?: boolean | null; needed?: boolean | null }
  cuda: { torch_installed: boolean; cuda_available: boolean | null }
  files: { all_present: boolean; missing_top_level: string[] }
  library_writable: boolean
  // Non-blocking heads-ups (old yt-dlp, package conflicts); fixed short text, no paths.
  warnings?: string[]
}

export type DiagnosticsHfCacheEntry = Schemas['DiagnosticsHfCacheEntry']

// A file or folder in a model folder outside the Hugging Face cache:
// torch.hub checkpoints (TORCH_HOME) or the audio-separator models.
export type DiagnosticsModelFolder = 'torch' | 'audio_separator'

export type DiagnosticsModelCache = Schemas['DiagnosticsModelCache']

export type DiagnosticsPyannoteReadiness = Schemas['DiagnosticsPyannoteReadiness']

export type DiagnosticsLogTail = Schemas['DiagnosticsLogTail']

export type DiagnosticsSupportReport = Schemas['DiagnosticsSupportReport']

export type DiagnosticsPackageInfo = Schemas['DiagnosticsPackageInfo']

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
  // Missing optional extras, installed one by one from its task row.
  optional_missing?: string[]
  approx_mb: number
}

export interface DiagnosticsInstallPresets {
  tasks: DiagnosticsInstallTask[]
  packages: Record<string, DiagnosticsPackageInfo>
  // Installed packages below the app's minimum that "Update these" upgrades.
  outdated?: string[]
}

export type DiagnosticsPackageUpdate = Schemas['DiagnosticsPackageUpdate']

export type DiagnosticsPackageUpdates = Schemas['DiagnosticsPackageUpdates']

// GET /api/diagnostics/gpu-torch and POST /api/diagnostics/gpu-torch/setup.
export type DiagnosticsGpuTorchNvidia = Schemas['DiagnosticsGpuTorchNvidia']

export type DiagnosticsTorchPackage = Schemas['DiagnosticsTorchPackage']

export interface DiagnosticsTorchVariant {
  variant: 'cu128' | 'cu126' | 'cpu'
  label: string
  index_url: string
  versions: Record<string, string>
  needs_nvidia: boolean
}

export type DiagnosticsTorchVerify = Schemas['DiagnosticsTorchVerify']

type GpuTorchState = 'missing' | 'mismatched' | 'cpu_on_gpu' | 'recommended' | 'different'

export interface DiagnosticsGpuTorchStatus {
  nvidia: DiagnosticsGpuTorchNvidia
  installed: DiagnosticsTorchPackage[]
  problems: string[]
  state: GpuTorchState
  python_supported: boolean
  recommended: DiagnosticsTorchVariant
  recommended_reason?: string | null
  variants: DiagnosticsTorchVariant[]
  probe: DiagnosticsTorchVerify | null
}

export type DiagnosticsResetResult = Schemas['DiagnosticsResetResult']

// POST /api/diagnostics/model-cache/hf/{revision}/delete and
// /model-cache/files/{folder}/{name}/delete (PC only).
export type DiagnosticsCacheDeleteResult = Schemas['DiagnosticsCacheDeleteResult']

// GET /api/diagnostics/remote-health: the last scheduled remote-access check
// (services/remote_health_service.py). States, whole days, Unix times and
// fixed messages only: never the public name, an address or a URL.
export type RemoteHealthState = 'off' | 'unknown' | 'ok' | 'warn' | 'critical'
/** GET /api/diagnostics/ports (PC only): numbers, flags and fixed text. */
export type PortEntry = Schemas['PortEntry']

export type PortsOverview = Schemas['PortsOverview']

export type RemoteHealth = Schemas['RemoteHealth']

// People and access > Public address check: the public-address check (PC only). The address
// is write-only: the API answers `configured` and, for Test, a state and a
// fixed message, never the address.
export type RemoteIpCheckStatus = Schemas['RemoteIpCheckStatus']

export type RemoteIpCheckTestResult = Schemas['RemoteIpCheckTestResult']
