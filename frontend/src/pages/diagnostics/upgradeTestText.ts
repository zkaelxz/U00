// Pure helpers for "Test first" (POST .../dependencies/{name}/test-upgrade,
// GET /api/diagnostics/upgrade-check): the verdict line and its details.
import type { DiagnosticsPackageUpdates } from '../../types/diagnostics'
import type { DiagnosticsUpgradeCheckState } from '../../types/diagnosticsInstalls'
import { jobRunning } from './jobPoll'

export const testConfirmLabel = (name: string, target: string) =>
  `Confirm test ${name} ${target} (takes minutes)`

/** Does this state belong to this package's current update target? */
export const testIsFor = (s: DiagnosticsUpgradeCheckState | null, name: string, target: string): boolean =>
  !!s && s.package === name && s.target === target

export type TestLine = { text: string; tone: 'ok' | 'warn' | 'error' | 'muted' }

/** "Testing…" while it runs; the verdict after; null when there is nothing for this target. */
export function testLine(s: DiagnosticsUpgradeCheckState | null, name: string, target: string): TestLine | null {
  if (!s || !testIsFor(s, name, target)) return null
  if (jobRunning(s.job)) return { text: `Testing ${name} ${target}…`, tone: 'muted' }
  if (s.job?.status === 'cancelled') return { text: `Test of ${name} ${target} was cancelled.`, tone: 'muted' }
  const r = s.result
  if (!r) return s.job?.status === 'error' ? { text: `Test of ${name} ${target} did not finish.`, tone: 'error' } : null
  const reason = r.reason ? `: ${r.reason}` : ''
  switch (r.verdict) {
    case 'safe': return { text: `Safe to update: ${name} ${target}${reason}.`, tone: 'ok' }
    case 'broken': return { text: `Not safe: ${name} ${target}${reason}.`, tone: 'error' }
    default: return { text: `Test of ${name} ${target}${reason}.`, tone: 'warn' }
  }
}

/** The detail lists, only the non-empty ones, in the Streamlit order. */
export function testDetails(s: DiagnosticsUpgradeCheckState): { title: string; lines: string[] }[] {
  const r = s.result
  const out: { title: string; lines: string[] }[] = []
  if (r?.conflicts?.length) out.push({ title: "Installed packages that say they don't support it", lines: r.conflicts })
  if (r?.new_failures?.length) out.push({ title: 'Fail with the update, pass today', lines: r.new_failures })
  if (r?.preexisting_failures?.length) out.push({ title: 'Already fail today', lines: r.preexisting_failures })
  if (s.output_tail.length) out.push({ title: 'Last lines of output', lines: s.output_tail })
  return out
}

/**
 * The server's last test (it runs on the server, so it survives leaving the tab)
 * when no package row on screen would show it: the update check that produced
 * its target is only held by the page, and a fresh visit has none.
 */
export function strandedTest(
  s: DiagnosticsUpgradeCheckState | null,
  updates: DiagnosticsPackageUpdates | null,
): { name: string; target: string } | null {
  if (!s?.package || !s.target || (!s.job && !s.result)) return null
  if (updates?.packages[s.package]?.target === s.target) return null
  return { name: s.package, target: s.target }
}
