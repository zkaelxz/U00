// Mirrors api/schemas/browser.py ("Install browser support").
import type { DiagnosticsJobState } from './diagnosticsInstalls'

export interface BrowserInstallResult {
  ok: boolean
  message: string
  output_tail: string[]
}

export interface BrowserInstallStatus {
  playwright_installed: boolean
  app_browser_installed: boolean
  system_browser_found: boolean
  free_mb: number
  required_mb: number
  refusal: string | null
  job_id: string
  job: DiagnosticsJobState | null
  last_result: BrowserInstallResult | null
}
