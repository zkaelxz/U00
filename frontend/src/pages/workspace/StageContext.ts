import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

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
  // The Simple title view: stages leave out their advanced controls. Hidden, never disabled, so nothing a run needs is lost.
  simple: boolean
  // Simple's Check button, pressed on the Review page: each new value opens the check queue at its first line.
  checkSignal?: number
  // The header's Search button pressed where the lines are: each new value shows All and focuses the lines' search box.
  searchSignal?: number
  // Search pressed on a page without the lines goes to Review first; the lines take that request once when they mount.
  takeSearch?: () => boolean
  // Where the header draws the Player button of an audio title's lines (its More row); null until the header has drawn it.
  playerDock?: HTMLElement | null
  // Simple's main button on a desktop wherever the lines are (Review and the title page), for the Need a look row to show first;
  // onShown tells the page whether that row drew it.
  checkLead?: CheckLead
  // Simple's title page of a title with lines still without English: Review's lines open on All, where those lines are.
  linesOnAll?: boolean
  // Simple's list of the series' episodes, shown as the last tab of Review's tools; unset outside a series.
  episodes?: ReactNode
}

export interface CheckLead {
  action: ReactNode
  onShown: (shown: boolean) => void
}

export const StageContext = createContext<StageContextValue | null>(null)

export function useStage(): StageContextValue {
  const ctx = useContext(StageContext)
  if (!ctx) throw new Error('useStage must be used inside WorkspaceShell')
  return ctx
}

/** True when the viewer chose Simple for this title kind; a stage hides its advanced controls then. */
export const useSimple = (): boolean => useContext(StageContext)?.simple ?? false

export const useCheckSignal = (): number => useContext(StageContext)?.checkSignal ?? 0

export const useSearchRequest = (): Pick<StageContextValue, 'searchSignal' | 'takeSearch'> => {
  const ctx = useContext(StageContext)
  return { searchSignal: ctx?.searchSignal, takeSearch: ctx?.takeSearch }
}

export const usePlayerDock = (): HTMLElement | null => useContext(StageContext)?.playerDock ?? null

export const useCheckLead = (): CheckLead | undefined => useContext(StageContext)?.checkLead

export const useLinesOnAll = (): boolean => useContext(StageContext)?.linesOnAll ?? false

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

/** The `lang` for a title's source text outside a line row, where no line
 *  language applies: it decides the glyph forms of shared kanji/hanzi. */
export function useTitleTextLang(): string {
  return useContext(StageContext)?.drama.source_language || 'zh'
}
