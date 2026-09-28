import { createContext, useContext } from 'react'

import type { DramaDetail } from '../../api/types'

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
