import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { getLineChapters } from '../../api/novelChapters'
import { useInterval } from '../../hooks/useInterval'
import { useJobs } from '../../hooks/useJobs'
import { useLatest } from '../../hooks/useLatest'
import type { NovelLineChapterRow } from '../../types/novelChapters'
import { activeJobsFor } from '../workspace/jobPillState'
import { LIVE_REFRESH_MS, pageSignature, translatingNote } from './liveRefresh'

/**
 * While a translate job runs on this title, checks the chapter counts every 10 s and calls `onChange` when the
 * page being read has gained English. `hold` (text selected, a line being edited) postpones the call to the next
 * check, so a reload never lands on something the reader is doing. Returns the note to show, or null when no
 * translation is running. Counts come from the line-chapters route, so a title with no saved chapters never refreshes.
 */
export function useLiveRefresh(dramaId: number, page: number | null, pageSize: number, hold: () => boolean, onChange: () => void) {
  const { jobs } = useJobs()
  const running = activeJobsFor(jobs ?? [], dramaId).some((j) => j.kind === 'translate')
  const [note, setNote] = useState<string | null>(null)
  const seen = useRef<string | null>(null)
  const pending = useRef(false)
  const latest = useLatest({ page, pageSize, hold, onChange })
  const wasRunning = useRef(false)

  const check = useCallback(async () => {
    let chapters: NovelLineChapterRow[]
    try {
      chapters = (await getLineChapters(dramaId)).chapters
    } catch {
      return // The next check tries again; the page on screen stays as it is.
    }
    const cur = latest.current
    setNote(translatingNote(chapters))
    if (cur.page === null) return
    const sig = pageSignature(chapters, cur.page, cur.pageSize)
    if (seen.current !== null && sig !== seen.current) pending.current = true
    seen.current = sig
    if (pending.current && !cur.hold()) {
      pending.current = false
      cur.onChange()
    }
  }, [dramaId, latest])

  // A new page or page size starts from its own counts.
  useEffect(() => {
    seen.current = null
    pending.current = false
  }, [dramaId, page, pageSize])

  useInterval(() => void check(), running ? LIVE_REFRESH_MS : null, { pauseWhenHidden: true })

  // One more look when the job ends, so the last batch shows, and again at the start so the baseline is the pre-run state.
  useEffect(() => {
    if (running !== wasRunning.current) {
      wasRunning.current = running
      void check()
    }
  }, [running, check])

  return running ? note ?? 'Translating' : null
}

/**
 * The Reader frame's side of the live refresh: tracks where the frame is scrolled and whether text is selected
 * in it (it reports both), reloads it through `reload` when new English arrives, and puts the scroll back once
 * the new document has loaded. `busy` holds a reload while a line is being edited or saved.
 */
export function useLiveFrame(
  dramaId: number,
  page: number | null,
  pageSize: number,
  frameRef: RefObject<HTMLIFrameElement | null>,
  busy: boolean,
  reload: () => void,
) {
  const state = useRef({ y: 0, selecting: false })
  const restoreY = useRef<number | null>(null)
  const busyNow = useLatest(busy)
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frameRef.current?.contentWindow) return
      const m = e.data as { baiheReader?: string; y?: unknown; selecting?: unknown }
      if (m?.baiheReader === 'scroll' && typeof m.y === 'number') state.current = { y: m.y, selecting: m.selecting === true }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [frameRef])
  const note = useLiveRefresh(
    dramaId,
    page,
    pageSize,
    () => state.current.selecting || busyNow.current,
    () => {
      restoreY.current = state.current.y
      reload()
    },
  )
  const onFrameLoad = useCallback(() => {
    if (restoreY.current === null) return
    frameRef.current?.contentWindow?.postMessage({ baiheReader: 'restore', y: restoreY.current }, '*')
    restoreY.current = null
  }, [frameRef])
  return { note, onFrameLoad }
}
