import { createContext } from 'react'

/** How a Card inside a <CardFoldContext.Provider> folds; see Card. */
export type CardFold = {
  storageKey: string
  defaultOpen: boolean
  // One-line current value shown while closed; without it the Card's own meta is used.
  summary?: string
  openSignal?: number
  expandSignal?: number
  closeSignal?: number
}

export const CardFoldContext = createContext<CardFold | null>(null)
