import { useEffect, type MutableRefObject } from 'react'

import { listAllLines } from '../../../../api/restructure'
import type { ReviewLine } from '../../../../types/review'
import type { EditState } from './LineRow'
import { registerLinesWriter, type LinesController, type LinesState } from './linesController'
import { draftFromLine, isDirty, type LineDraft } from './reviewDraft'

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>

export interface ParkedDraft {
  base: ReviewLine
  draft: LineDraft
}

const PREFIX = 'baihe.review.draft.'
const parkKey = (dramaId: number, lineId: number) => `${PREFIX}${dramaId}.${lineId}`

function browserSession(): Store | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

export function parkDraft(dramaId: number, edit: EditState, store = browserSession()): void {
  try {
    store?.setItem(parkKey(dramaId, edit.lineId), JSON.stringify({ base: edit.base, draft: edit.draft }))
  } catch {
    // Storage full or blocked: the unmount save is the only copy.
  }
}

export function unparkDraft(dramaId: number, lineId: number, store = browserSession()): void {
  try {
    store?.removeItem(parkKey(dramaId, lineId))
  } catch {
    // Nothing to clear.
  }
}

function isParked(x: unknown): x is ParkedDraft {
  const p = x as ParkedDraft
  return Boolean(p?.base && p.draft) && typeof p.base.id === 'number' && typeof p.draft.en === 'string'
}

/** The first draft parked for this drama, or null. */
export function readParkedDraft(dramaId: number, store = browserSession()): ParkedDraft | null {
  if (!store) return null
  try {
    const prefix = `${PREFIX}${dramaId}.`
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i)
      if (!k?.startsWith(prefix)) continue
      const p: unknown = JSON.parse(store.getItem(k) ?? 'null')
      if (isParked(p)) return p
      store.removeItem(k)
    }
  } catch {
    // Unreadable: nothing to offer.
  }
  return null
}

/** The draft moved onto the line as it is now: fields the draft left alone
 *  follow the saved line, typed fields keep the typing. */
export function rebaseDraft(oldBase: ReviewLine, fresh: ReviewLine, draft: LineDraft): LineDraft {
  const was = draftFromLine(oldBase)
  const now = draftFromLine(fresh)
  const out = { ...draft }
  for (const k of Object.keys(draft) as (keyof LineDraft)[]) {
    if (draft[k] === was[k]) (out as Record<string, unknown>)[k] = now[k]
  }
  return out
}

// The unmount save outlives the panel; a Review opened again meanwhile waits
// for it, so it doesn't offer back a draft that is about to be saved.
const leavingSaves = new Map<number, Promise<unknown>>()

/** Leaving Review with a dirty draft: park it, then save it. Nothing waits for
 *  that save, so the parked copy is what survives a 409 or a lost connection. */
export function leaveWithDraft(ctl: LinesController, edit: EditState | null, store = browserSession()): Promise<unknown> | null {
  if (!edit || !ctl.stillDirty(edit.lineId)) return null
  const { dramaId } = ctl
  parkDraft(dramaId, edit, store)
  const run = ctl.saveEdit().then((ok) => {
    if (ok) unparkDraft(dramaId, edit.lineId, store)
  })
  leavingSaves.set(dramaId, run)
  void run.finally(() => {
    if (leavingSaves.get(dramaId) === run) leavingSaves.delete(dramaId)
  })
  return run
}

/** Opening Review: put a parked draft back in the editor, rebased on the line
 *  as it is now. Dropped when the line is gone or already says the same. */
export async function offerParkedDraft(
  ctl: LinesController,
  cancelled: () => boolean,
  store = browserSession(),
  loadLines: (dramaId: number) => Promise<ReviewLine[]> = listAllLines,
): Promise<void> {
  const { dramaId } = ctl
  if (!readParkedDraft(dramaId, store)) return
  await leavingSaves.get(dramaId)
  const parked = readParkedDraft(dramaId, store)
  if (!parked || cancelled()) return
  let fresh: ReviewLine | undefined
  try {
    fresh = (await loadLines(dramaId)).find((l) => l.id === parked.base.id)
  } catch {
    // Kept for the next visit.
    return
  }
  if (cancelled()) return
  if (!fresh) {
    unparkDraft(dramaId, parked.base.id, store)
    ctl.setStatus('An unsaved edit could not be brought back: its line no longer exists.')
    return
  }
  const draft = rebaseDraft(parked.base, fresh, parked.draft)
  if (!isDirty(fresh, draft) || ctl.restoreDraft(fresh, draft)) unparkDraft(dramaId, parked.base.id, store)
}

// A dirty draft is not lost silently: leaving the page asks first; leaving
// the stage (unmount) parks it in this tab and saves it, and if that save
// fails, the next visit to Review offers it back.
export function useDraftGuard(edit: EditState | null, st: MutableRefObject<LinesState>, ctl: LinesController) {
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
  useEffect(() => {
    const unregister = registerLinesWriter(ctl)
    let cancelled = false
    void offerParkedDraft(ctl, () => cancelled)
    return () => {
      cancelled = true
      unregister()
      leaveWithDraft(ctl, st.current.edit)
    }
  }, [ctl, st])
}
