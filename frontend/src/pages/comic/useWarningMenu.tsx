// The reader bar's warnings menu: loads the run notes and marks the boxes a warning names after a jump.

import { useCallback, useEffect, useMemo, useState } from 'react'

import { scanlateApi } from '../../api/scanlate'
import type { ScanlatePageNotes } from '../../types/scanlate'
import { warningPages } from './scanlateLogic'
import { WarningMenu } from './WarningPages'

// `reloadOn` changes whenever the page list reloads (an upload or a finished run), which is when the notes change.
export function useWarningMenu(dramaId: number, reloadOn: unknown, current: number | null, go: (n: number, fromScroll: boolean, free: boolean) => void) {
  const [notes, setNotes] = useState<ScanlatePageNotes[]>([])
  useEffect(() => {
    let cancelled = false
    scanlateApi.runNotes(dramaId).then(
      (n) => !cancelled && setNotes(n.pages),
      () => {},
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloadOn])
  const warnings = useMemo(() => warningPages(notes), [notes])
  const [flash, setFlash] = useState<{ page: number; boxes: number[] } | null>(null)
  const goToWarning = useCallback(
    (n: number, boxes: number[]) => {
      go(n, false, true)
      setFlash(boxes.length ? { page: n, boxes } : null)
    },
    [go],
  )
  const warningMenu = <WarningMenu all={warnings.all} groups={warnings.groups} pageNumber={current} onGo={goToWarning} />
  return { warningMenu, flash, goToWarning }
}
