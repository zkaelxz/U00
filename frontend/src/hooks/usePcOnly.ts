/*
 * usePcOnly(): 'local' | 'remote' | 'unknown' -- is this page on the main PC?
 * The state lives in api/pcOnly.ts (a module store, so no provider is needed
 * in App.tsx); the first caller triggers one GET /api/meta.
 *
 *   const pc = usePcOnly()
 *   {pc === 'remote' ? <p className="muted">Deleting is PC only.</p> : <ConfirmButton ... />}
 *
 * Remote-mode rules (docs spec "Shared pieces"):
 *   - a whole PC-only block keeps its Section title, summary "PC only", body
 *     "Run this on the main PC." (see PC_ONLY_SUMMARY / PC_ONLY_BODY);
 *   - per-row PC-only deletes are not rendered; show one muted line instead;
 *   - 'unknown' renders like 'local' (optimistic; the server enforces),
 *     except the Diagnostics/Settings admin blocks (packages, danger zone,
 *     extension), which wait for 'local' and show usePcPendingNote() meanwhile.
 * Send PC-only mutations with pcOnlyFetch() from api/pcOnly.ts.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { getPcMetaFailed, getPcMode, loadPcMode, subscribePcMode, type PcMode } from '../api/pcOnly'

export type { PcMode }

export const PC_ONLY_SUMMARY = 'PC only'
export const PC_ONLY_BODY = 'Run this on the main PC.'
export const PC_ONLY_DELETE_NOTE = 'Deleting is PC only.'
export const PC_CHECKING = 'Checking whether this is the main PC…'
export const PC_UNCONFIRMED = "Couldn't confirm this is the main PC."

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
