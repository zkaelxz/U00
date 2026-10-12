// Which glossary scan to show when the person comes back. The server holds one scan per source
// (novel, lines); both are read, because looking at the wrong one reads "idle" and hides the
// scan that is running or waiting to be reviewed.
import { isActiveStatus, isResumableScan } from './autotuneGlossary'
import type { GlossarySource } from './glossaryExtract'

export interface ScanStatus {
  status: string
  proposals?: unknown[] | null
}

// null = no scan held for that source (the server answers "idle") or not read yet.
export type Scans = Record<GlossarySource, ScanStatus | null>

const SOURCES: GlossarySource[] = ['lines', 'novel']

// The scan to surface: a running one first, then one with something to review or a failure.
// `prefer` wins a tie, so the source the bar already shows doesn't flip.
function heldScan(scans: Scans, prefer: GlossarySource | null): { source: GlossarySource; running: boolean } | null {
  const order = prefer ? [prefer, ...SOURCES.filter((s) => s !== prefer)] : SOURCES
  const running = order.find((s) => isActiveStatus(scans[s]?.status))
  if (running) return { source: running, running: true }
  const waiting = order.find((s) => scans[s] && isResumableScan(scans[s]!))
  return waiting ? { source: waiting, running: false } : null
}

export function sourceToShow(args: {
  // A choice made on this visit.
  userPick: GlossarySource | null
  // The choice remembered from an earlier visit.
  storedPick: GlossarySource | null
  scans: Scans
  defaultSource: GlossarySource
}): GlossarySource {
  const { userPick, storedPick, scans, defaultSource } = args
  if (userPick) return userPick
  return heldScan(scans, storedPick)?.source ?? storedPick ?? defaultSource
}

export interface ScanBanner {
  kind: 'running' | 'ready' | 'failed'
  source: GlossarySource
  count: number
}

export function scanBanner(scans: Scans, prefer: GlossarySource | null = null): ScanBanner | null {
  const held = heldScan(scans, prefer)
  if (!held) return null
  const s = scans[held.source]!
  if (held.running) return { kind: 'running', source: held.source, count: 0 }
  if (s.status === 'error') return { kind: 'failed', source: held.source, count: 0 }
  return { kind: 'ready', source: held.source, count: s.proposals?.length ?? 0 }
}

export function scanBannerText(b: ScanBanner): string {
  if (b.kind === 'running') return 'A glossary scan is running'
  if (b.kind === 'failed') return 'The glossary scan failed'
  return `Scan finished: ${b.count} proposal${b.count === 1 ? '' : 's'} to review`
}

export const scanBannerButton = (b: ScanBanner): string =>
  b.kind === 'running' ? 'Show progress' : b.kind === 'failed' ? 'See details' : 'Review proposals'

// The Glossary section's closed-state summary, so a folded section still says a scan is waiting.
export function scanSummary(b: ScanBanner): string {
  if (b.kind === 'running') return 'scan running'
  if (b.kind === 'failed') return 'scan failed'
  return `scan finished, ${b.count} to review`
}
