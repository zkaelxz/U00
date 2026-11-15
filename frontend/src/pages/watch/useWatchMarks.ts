/** The Watch page's Good, Wrong and Mixed on the line being spoken: one press saves, and the page's list is updated at once. */
import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from 'react'

import { ApiError } from '../../api/client'
import { listAllLines, setMark, setReviewed } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import { verdictChange, type Verdict } from './watchLogic'

const SAID: Record<Verdict, string> = { good: 'Marked as looks right.', wrong: 'Marked wrong.', mixed: 'Marked mixed.' }
const CHANGED = 'This line changed. Check it, then mark again.'
const FAILED = 'The mark could not be saved. Try again in a moment.'

export function useWatchMarks(id: number, setLines: Dispatch<SetStateAction<ReviewLine[] | null>>) {
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  // A ref as well as state: a key pressed right after another is judged by what is true now, not by the last render.
  const running = useRef(false)

  const mark = useCallback(async (line: ReviewLine | null, verdict: Verdict) => {
    if (!line || running.current) return
    const change = verdictChange(line, verdict)
    running.current = true
    setBusy(true)
    try {
      // The English being judged goes with it: the server refuses a mark on text that has since changed.
      const saved = change.kind === 'reviewed'
        ? await setReviewed(id, line.id, change.to, line.en)
        : await setMark(id, line.id, change.to, line.en)
      setLines((ls) => ls?.map((l) => (l.id === saved.id ? saved : l)) ?? ls)
      setStatus(change.kind === 'reviewed' ? (change.to ? SAID.good : 'Check taken off.') : change.to ? SAID[verdict] : 'Mark cleared.')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setStatus(CHANGED)
        void listAllLines(id).then((rows) => setLines(rows), () => undefined)
      } else setStatus(FAILED)
    } finally {
      running.current = false
      setBusy(false)
    }
  }, [id, setLines])

  return { markStatus: status, markBusy: busy, mark, say: setStatus }
}
