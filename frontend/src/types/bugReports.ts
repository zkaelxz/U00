// The "Report a problem" models in api/schemas/bug_report.py (BugReport*); responses come from the generated schema.
import type { components } from '../api/generated/schema'
import type { ConsoleEntry, ErrorEntry, FailedRequest, RouteVisit } from '../report/capture'

type Schemas = components['schemas']

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

export type BugReportSaved = Schemas['BugReportSaved']
export type BugReportText = Schemas['BugReportText']
export type BugReportListItem = Schemas['BugReportListItem']
export type BugReportDeleted = Schemas['BugReportDeleted']
