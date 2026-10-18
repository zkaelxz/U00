/*
 * usePcOnly(): 'local' | 'remote' | 'unknown' -- is this page on the main PC?
 * The state lives in api/pcOnly.ts (a module store, so no provider is needed
 * in App.tsx); the first caller triggers one GET /api/meta.
 *
 *   const pc = usePcOnly()
 *   {pc !== 'remote' && <ConfirmButton ... />}
 *
 * Remote-mode rules:
 *   - PC-only blocks, tabs, controls and their explanatory notes are not
 *     rendered at all (Settings leaves out PC_ONLY_CARDS, Library tools and
 *     Sources drop their PC-only tabs); a page that would end up empty says so
 *     in one line;
 *   - 'unknown' renders like 'local' (optimistic; the server enforces),
 *     except the Diagnostics/Settings admin blocks (packages, danger zone,
 *     extension) and Library tools' Storage and Data folder tabs (they read
 *     the PC's disk on mount), which wait for 'local' and show
 *     usePcPendingNote() meanwhile.
 * Send PC-only mutations with pcOnlyFetch() from api/pcOnly.ts.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { getPcMetaFailed, getPcMode, loadPcMode, subscribePcMode, type PcMode } from '../api/pcOnly'

export type { PcMode }

const PC_CHECKING = 'Checking whether this is the main PC…'
const PC_UNCONFIRMED = "Couldn't confirm this is the main PC."

export function usePcOnly(): PcMode {
  const mode = useSyncExternalStore(subscribePcMode, getPcMode, getPcMode)
  useEffect(() => {
    void loadPcMode()
  }, [])
  return mode
}

/** While the mode is 'unknown': the muted line to show in place of PC-only controls. */
export function usePcPendingNote(pc: PcMode): string | null {
  const failed = useSyncExternalStore(subscribePcMode, getPcMetaFailed, getPcMetaFailed)
  if (pc !== 'unknown') return null
  return failed ? PC_UNCONFIRMED : PC_CHECKING
}
