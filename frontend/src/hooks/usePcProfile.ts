/*
 * usePcProfile(): the PC window's Owner/Guest state from /api/meta, or null
 * where profiles do not apply (a remote device, sign-in on, or an old server).
 * It never holds the page back: the server is already on Guest until the
 * Owner unlocks, so what shows before /api/meta answers is Guest's view.
 */
import { useSyncExternalStore } from 'react'

import { getPcProfileStatus, subscribePcMode } from '../api/pcOnly'

export function usePcProfile() {
  const status = useSyncExternalStore(subscribePcMode, getPcProfileStatus, getPcProfileStatus)
  return { status }
}
