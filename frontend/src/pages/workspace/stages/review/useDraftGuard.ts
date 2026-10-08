import { useEffect, type MutableRefObject } from 'react'

import type { EditState } from './LineRow'
import type { createLinesController, LinesState } from './linesController'
import { isDirty } from './reviewLogic'

// A dirty draft is never lost silently: leaving the page asks first, and
// leaving the stage (unmount) saves it.
export function useDraftGuard(edit: EditState | null, st: MutableRefObject<LinesState>, ctl: ReturnType<typeof createLinesController>) {
  const dirtyNow = edit !== null && isDirty(edit.base, edit.draft)
  useEffect(() => {
    if (!dirtyNow) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirtyNow])
  useEffect(
    () => () => {
      const cur = st.current.edit
      if (cur && ctl.stillDirty(cur.lineId)) void ctl.saveEdit()
    },
    [ctl, st],
  )
}
