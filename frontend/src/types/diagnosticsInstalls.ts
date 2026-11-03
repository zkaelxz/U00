// Mirrors api/diagnostics_install_schemas.py (package install and GPU PyTorch
// setup, Deno install, "Test first").
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

import type { DiagnosticsTorchVerify } from './diagnostics'

export type DiagnosticsJobState = Schemas['DiagnosticsJobState']

export type DiagnosticsJobStarted = Schemas['DiagnosticsJobStarted']

export type DiagnosticsDenoStatus = Schemas['DiagnosticsDenoStatus']

type UpgradeVerdict = 'safe' | 'broken' | 'conflict' | 'incomplete' | string

interface DiagnosticsUpgradeCheckResult {
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

export type DiagnosticsDependencyInstallState = Schemas['DiagnosticsDependencyInstallState']

export type DiagnosticsCudaRuntimeStatus = Schemas['DiagnosticsCudaRuntimeStatus']
