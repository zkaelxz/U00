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
