/*
 * usePcProfile(): the PC window's profile state, or null when profiles do not
 * apply (no PIN set, not the PC, or the call failed). One GET /api/pc-profile
 * per page load; `refresh` re-reads it after a change.
 */
import { useCallback, useEffect, useState } from 'react'

import { getPcProfile, type PcProfileStatus } from '../api/pcProfile'
import type { PcMode } from '../api/pcOnly'

export function usePcProfile(pc: PcMode, enabled: boolean) {
  const [status, setStatus] = useState<PcProfileStatus | null>(null)
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setStatus(await getPcProfile())
    } catch {
      setStatus(null)
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    if (enabled && pc !== 'remote') void refresh()
    else setLoaded(true)
  }, [enabled, pc, refresh])

  return { status, loaded, refresh }
}
