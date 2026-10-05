import { createContext, useContext, useEffect, useState } from 'react'

import type { DramaDetail } from '../../api/types'
import { useRoute, type DramaFocus } from '../../router'

export interface StageContextValue {
  dramaId: number
  drama: DramaDetail
  // Reload the drama header/detail; call after anything that changes it.
  refetchDrama: () => void
  // Call when a background job the stage started reaches a terminal state.
  // Refetches drama data (lines/other stage data are the stage's own to reload).
  onJobDone: () => void
}

export const StageContext = createContext<StageContextValue | null>(null)

export function useStage(): StageContextValue {
  const ctx = useContext(StageContext)
  if (!ctx) throw new Error('useStage must be used inside WorkspaceShell')
  return ctx
}

/**
 * For a panel a deep link ("?focus=glossary") points at: a Section openSignal that
 * changes after mount (a signal present at mount would not open a closed Section),
 * and a scroll to `elementId` once the open has rendered.
 */
export function useStageFocus(target: DramaFocus, elementId: string, ready = true): number {
  const route = useRoute()
  // `ready`: the content above the panel has loaded, so the scroll does not land before it shifts the panel down.
  const wanted = ready && route.name === 'drama' && route.focus === target
  const [signal, setSignal] = useState(0)
  useEffect(() => {
    if (wanted) setSignal((n) => n + 1)
  }, [wanted])
  useEffect(() => {
    if (signal > 0) document.getElementById(elementId)?.scrollIntoView({ block: 'start' })
  }, [signal, elementId])
  return signal
}
