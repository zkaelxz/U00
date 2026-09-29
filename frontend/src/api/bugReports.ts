// "Report a problem" (api/routers/bug_report_routes.py). Submitting is open
// to anyone who can use the app; listing/reading needs diagnostics access;
// delete is PC only (pcOnlyFetch).
import type {
  BugReportClient, BugReportDeleted, BugReportListItem, BugReportSaved, BugReportText,
} from '../types/bugReports'
import { getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/diagnostics/bug-reports'

// Server caps (services/bug_report_service.py).
export const SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024
export const SCREENSHOT_TYPES = ['image/png', 'image/jpeg'] as const

export function submitBugReport(report: BugReportClient, screenshot: File | null, f?: Fetch) {
  const form = new FormData()
  form.append('report', JSON.stringify(report))
  if (screenshot) form.append('screenshot', screenshot, screenshot.name)
  return postMultipart<BugReportSaved>(BASE, form, f)
}

export const listBugReports = (f?: Fetch) => getJson<BugReportListItem[]>(BASE, f)

export const getBugReport = (id: number, f?: Fetch) => getJson<BugReportText>(`${BASE}/${id}`, f)

// `stamp` (from the list) pins the delete to that report's folder.
export const deleteBugReport = (id: number, stamp: string, f?: Fetch) =>
  postJson<BugReportDeleted>(`${BASE}/${id}/delete`, { confirm: true, stamp }, pcOnlyFetch(f))
