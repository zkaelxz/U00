import { useEffect, useState } from 'react'

import { getTranscribeConfig } from '../../../../api/workspace'
import { canRetranscribe } from './retranscribeLogic'

export function useCanRetranscribeLine(dramaId: number) {
  const [can, setCan] = useState(false)
  useEffect(() => {
    let cancelled = false
    setCan(false)
    getTranscribeConfig(dramaId).then(
      (cfg) => !cancelled && setCan(canRetranscribe(cfg)),
      () => {},
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])
  return can
}
