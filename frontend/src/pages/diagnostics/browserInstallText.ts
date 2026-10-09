// Pure helpers for the Setup card's "Install browser support" row
// (GET /api/diagnostics/browser).
import type { BrowserInstallStatus } from '../../types/browserInstall'
import { jobRunning } from './jobPoll'

export const BROWSER_INTRO =
  'Downloads about 150 MB. Lets the app read JavaScript-heavy source sites like baihehub and Fanjiao. ' +
  'Not needed if Chrome or Edge is installed.'
export const BROWSER_CONFIRM = 'Confirm install browser support (about 150 MB)'

/** Show the row: no Chrome or Edge, or a job or result to report. */
export const showBrowser = (s: BrowserInstallStatus | null): boolean =>
  !!s && (!s.system_browser_found || jobRunning(s.job) || !!s.last_result)

/** Can the install button be offered (ignoring other running jobs)? */
export const canOfferBrowser = (s: BrowserInstallStatus): boolean =>
  !s.refusal && !jobRunning(s.job)

/** The state in one line, or null while a job runs. */
export function browserStatusLine(s: BrowserInstallStatus): string | null {
  if (jobRunning(s.job)) return null
  if (s.app_browser_installed) return 'Browser support is installed.'
  return s.refusal
}

type ResultLine = { text: string; tone: 'ok' | 'error' }

/** The last install's outcome, or null. */
export function browserResultLine(s: BrowserInstallStatus): ResultLine | null {
  const r = s.last_result
  if (!r || jobRunning(s.job)) return null
  return r.ok ? { text: r.message || 'Browser support installed.', tone: 'ok' }
    : { text: r.message || 'The browser install failed.', tone: 'error' }
}
