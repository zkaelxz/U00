// Pure helpers for the Setup card's "Install Deno" block
// (GET /api/diagnostics/deno). yt-dlp needs a JS runtime for YouTube formats.
import type { DiagnosticsDenoStatus } from '../../types/diagnosticsInstalls'
import { jobRunning } from './jobPoll'

// Approximate: the official release zip (winget downloads the same build).
export const DENO_SIZE = 'about 45 MB'
export const DENO_CONFIRM = `Confirm install Deno (${DENO_SIZE})`

/** Show the block: no JS runtime, a job running, or a result to report. */
export const showDeno = (s: DiagnosticsDenoStatus | null): boolean =>
  !!s && (!s.runtime_found || jobRunning(s.job) || !!s.last_result)

/** Can "Install Deno…" be offered at all (ignoring running jobs)? */
export const canOfferDeno = (s: DiagnosticsDenoStatus): boolean =>
  s.can_install && !s.deno_installed && !s.deno_on_path && !jobRunning(s.job)

/** Why the install isn't offered here, or null. */
export function denoNote(s: DiagnosticsDenoStatus): string | null {
  if (jobRunning(s.job)) return null
  if (s.deno_installed && !s.deno_on_path)
    return 'Deno is installed but Baihe cannot see it yet. Restart Baihe (and its terminal) so it is found on PATH.'
  if (!s.runtime_found && !s.can_install)
    return 'There is no Deno download for this system here. Install Deno, Node, Bun or QuickJS yourself.'
  return null
}

export type DenoResultLine = { text: string; tone: 'ok' | 'warn' | 'error' }

/** The last install's outcome, or null. */
export function denoResultLine(s: DiagnosticsDenoStatus): DenoResultLine | null {
  const r = s.last_result
  if (!r || jobRunning(s.job)) return null
  if (!r.ok) return { text: r.message || 'The Deno install failed.', tone: 'error' }
  if (r.needs_restart) return { text: r.message || 'Deno was installed. Restart Baihe to use it.', tone: 'warn' }
  return { text: 'Deno installed.', tone: 'ok' }
}

export const denoIntro = (installMethod: string): string =>
  installMethod === 'winget'
    ? 'Some video sites (YouTube first) lose formats without a JavaScript runtime. Installs Deno with winget.'
    : "Some video sites (YouTube first) lose formats without a JavaScript runtime. Downloads Deno's official release and checks its checksum."
