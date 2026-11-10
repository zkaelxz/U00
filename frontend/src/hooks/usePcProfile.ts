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
  const [fetched, setFetched] = useState(false)
  const [reads, setReads] = useState(0)
  const wanted = enabled && pc !== 'remote'

  useEffect(() => {
    if (!wanted) return
    let stale = false
    getPcProfile()
      .then((next) => !stale && setStatus(next), () => !stale && setStatus(null))
      .finally(() => !stale && setFetched(true))
    return () => {
      stale = true
    }
  }, [wanted, reads])

  const refresh = useCallback(() => {
    setReads((n) => n + 1)
    return Promise.resolve()
  }, [])

  return { status, loaded: !wanted || fetched, refresh }
}
