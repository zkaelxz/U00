import { useEffect, useState } from 'react'

import { getTranscribeConfig } from '../../../../api/workspace'
import { canRetranscribe } from './retranscribeLogic'

export function useCanRetranscribeLine(dramaId: number) {
  // Keyed by title so another title reads false until its own answer arrives.
  const [answer, setAnswer] = useState<{ dramaId: number; can: boolean } | null>(null)
  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (cfg) => !cancelled && setAnswer({ dramaId, can: canRetranscribe(cfg) }),
      () => {},
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])
  return answer?.dramaId === dramaId && answer.can
}
