// Mirrors api/diagnostics_install_schemas.py (package install and GPU PyTorch
// setup, Deno install, "Test first").
import type { DiagnosticsTorchVerify } from './diagnostics'

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

type UpgradeVerdict = 'safe' | 'broken' | 'conflict' | 'incomplete' | string

export interface DiagnosticsUpgradeCheckResult {
  ok: boolean
  verdict: UpgradeVerdict | null
  reason: string | null
  version: string | null
  new_failures: string[] | null
  preexisting_failures: string[] | null
  conflicts: string[] | null
}

export interface DiagnosticsUpgradeCheckState {
  package: string | null
  target: string | null
  output_tail: string[]
  result: DiagnosticsUpgradeCheckResult | null
  // Set when this copy has no test suite (an installed copy): hide Test first.
  unavailable_reason: string | null
  job_id: string
  job: DiagnosticsJobState | null
}

export interface DiagnosticsDependencyInstallResult {
  package: string
  ok: boolean
  output_tail: string[]
  // Plain-English next step for a known failure (pip's cache unwritable), or the cancel note.
  hint: string | null
  cancelled: boolean
  // GPU PyTorch setup only.
  variant: string | null
  verify: DiagnosticsTorchVerify | null
}

export interface DiagnosticsDependencyInstallState {
  kind: 'package' | 'gpu_torch' | null
  package: string | null
  result: DiagnosticsDependencyInstallResult | null
  job_id: string
  job: DiagnosticsJobState | null
}
