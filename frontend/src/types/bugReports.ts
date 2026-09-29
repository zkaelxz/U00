// Mirrors the "Report a problem" models in api/schemas.py (BugReport*).
import type { ConsoleEntry, ErrorEntry, FailedRequest, RouteVisit } from '../report/capture'

export type ReportMode = 'pc' | 'lan' | 'remote' | 'unknown'

export interface BugReportClient {
  what_happened: string
  expected: string
  include_server_log: boolean
  route: string
  route_history: RouteVisit[]
  console: ConsoleEntry[]
  errors: ErrorEntry[]
  failed_requests: FailedRequest[]
  app_version: string
  api_version: string
  environment: string
  build_id: string
  user_agent: string
  viewport: { width: number; height: number; dpr?: number } | null
  mode: ReportMode
}

export interface BugReportSaved {
  id: number
  // The server section (commit, setup, log) is included only for admins.
  markdown: string
}

export interface BugReportListItem {
  id: number
  created_at: string | null
  summary: string
  route: string | null
  mode: string | null
  has_screenshot: boolean
  has_server_log: boolean
}

export interface BugReportDeleted {
  id: number
  deleted: boolean
}
