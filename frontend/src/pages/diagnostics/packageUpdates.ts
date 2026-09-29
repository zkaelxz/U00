// Pure helpers for Packages > Installed packages: the installed version and
// the result of an explicit "Check for updates" (PyPI, on the server). No
// Update button is offered before a check, or when the check found no
// allowed update.
import type { DiagnosticsPackageUpdate, DiagnosticsPackageUpdates } from '../../types/diagnostics'

/** "1.2.3" shown as "v1.2.3"; null when unknown. */
export const versionLabel = (v: string | null | undefined): string | null => (v ? `v${v}` : null)

export type UpdateLine = { text: string; tone: 'ok' | 'warn' | 'muted' }

/** What the row says after a check (the Update button carries the target itself). */
export function updateLine(u: DiagnosticsPackageUpdate): UpdateLine {
  switch (u.status) {
    case 'up_to_date':
      return { text: 'Up to date', tone: 'ok' }
    case 'update':
      return u.reason && u.latest && u.latest !== u.target
        ? { text: `Update to ${u.target} available (newer ${u.latest} is ${u.reason})`, tone: 'muted' }
        : { text: `Update to ${u.target} available`, tone: 'muted' }
    case 'held_back':
      return { text: `Newer ${u.latest} exists but is ${u.reason ?? 'held back'}`, tone: 'warn' }
    case 'managed':
      return { text: 'Updated with GPU PyTorch (above)', tone: 'muted' }
    default:
      return { text: "Couldn't check (PyPI didn't answer)", tone: 'muted' }
  }
}

/** True when the row gets an Update button. */
export const canUpdate = (u: DiagnosticsPackageUpdate | undefined): u is DiagnosticsPackageUpdate & { target: string } =>
  !!u && u.status === 'update' && !!u.target

/** "2 updates available, 1 held back" / "Everything is up to date". */
export function updatesSummary(r: DiagnosticsPackageUpdates): string {
  const all = Object.values(r.packages)
  const n = all.filter((u) => u.status === 'update').length
  const held = all.filter((u) => u.status === 'held_back').length
  const unknown = all.filter((u) => u.status === 'unknown').length
  const parts: string[] = []
  if (n) parts.push(`${n} ${n === 1 ? 'update' : 'updates'} available`)
  if (held) parts.push(`${held} held back`)
  if (unknown) parts.push(`${unknown} couldn't be checked`)
  return parts.length ? parts.join(', ') : 'Everything is up to date'
}

