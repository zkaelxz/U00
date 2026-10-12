// Wording for the "CUDA runtime for GPU Whisper" row (GET /api/diagnostics/cuda-runtime).
import type { DiagnosticsCudaRuntimeStatus } from '../../types/diagnosticsInstalls'
import { jobRunning } from './jobPoll'

// Approximate: the cuBLAS 12 and cuDNN 9 wheels together.
export const CUDA_RUNTIME_CONFIRM = 'Confirm install (about 1.2 GB)'

export const CUDA_RUNTIME_INTRO =
  "Whisper can't use the GPU because its CUDA libraries (cuBLAS 12 and cuDNN 9) weren't found, " +
  'so it runs on the CPU. Installing them takes about 1.2 GB and needs no restart.'

/** The last install's outcome (or "found" once the libraries are in place), or null. */
export function cudaRuntimeResultLine(s: DiagnosticsCudaRuntimeStatus): { text: string; tone: 'ok' | 'error' } | null {
  const install = s.install
  if (!install || jobRunning(install.job)) return null
  if (install.result && !install.result.ok) {
    return { text: install.result.hint || 'The install failed. See Packages for details.', tone: 'error' }
  }
  if (!s.missing) return { text: 'Installed. Whisper can use the GPU now.', tone: 'ok' }
  return null
}
