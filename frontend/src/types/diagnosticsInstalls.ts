// Mirrors api/diagnostics_install_schemas.py (package install and GPU PyTorch
// setup, Deno install, "Test first").
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

import type { DiagnosticsTorchVerify } from './diagnostics'

export type DiagnosticsJobState = Schemas['DiagnosticsJobState']

export type DiagnosticsJobStarted = Schemas['DiagnosticsJobStarted']

export type DiagnosticsDenoStatus = Schemas['DiagnosticsDenoStatus']

export type DiagnosticsUpgradeCheckState = Schemas['DiagnosticsUpgradeCheckState']

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
