import type { DiagnosticsOverview } from '../types/diagnostics'
import { getJson } from './client'

export const getDiagnostics = (f?: typeof fetch) =>
  getJson<DiagnosticsOverview>('/api/diagnostics', f)
