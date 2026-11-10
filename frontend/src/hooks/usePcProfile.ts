/*
 * usePcProfile(): the PC window's Owner/Guest state from /api/meta, or null
 * where profiles do not apply (a remote device, sign-in on, or an old server).
 * `loaded` turns true once /api/meta has answered or failed, so the app can
 * wait for it before choosing between the picker and the page.
 */
import { useSyncExternalStore } from 'react'

import { getPcMetaFailed, getPcMode, getPcProfileStatus, subscribePcMode } from '../api/pcOnly'

export function usePcProfile() {
  const status = useSyncExternalStore(subscribePcMode, getPcProfileStatus, getPcProfileStatus)
  const mode = useSyncExternalStore(subscribePcMode, getPcMode, getPcMode)
  const failed = useSyncExternalStore(subscribePcMode, getPcMetaFailed, getPcMetaFailed)
  return { status, loaded: mode !== 'unknown' || failed }
}
