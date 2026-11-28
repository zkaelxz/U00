/** The Watch page's left and right ear marks by line id, worked out the first time someone who may start jobs watches a stereo title. */
import { useEffect, useState } from 'react'

import { readerApi } from '../../api/reader'
import type { EarMarks, EarSide } from '../../types/reader'

const POLL_MS = 3000
const NO_MARKS: ReadonlyMap<number, EarSide> = new Map()

/** What to do after an answer: ask the PC to work the marks out (once per visit), ask again shortly, or stop. */
export function earMarksNext(state: EarMarks['state'], canStart: boolean, asked: boolean): 'start' | 'poll' | 'stop' {
  if (state === 'working') return 'poll'
  // "needed" again after asking means the PC could not start it (no ffmpeg); asking again would not help.
  if (state === 'needed' && canStart && !asked) return 'start'
  return 'stop'
}

export function earMarkMap(answer: EarMarks): ReadonlyMap<number, EarSide> {
  return answer.state === 'ready' ? new Map(answer.marks.map((m) => [m.line_id, m.side])) : NO_MARKS
}

export function useEarMarks(id: number, canStart: boolean): ReadonlyMap<number, EarSide> {
  // Kept with the title it is for, so another title's marks never show while its own are asked for.
  const [got, setGot] = useState<{ id: number; marks: ReadonlyMap<number, EarSide> } | null>(null)
  useEffect(() => {
    let cancelled = false
    let timer = 0
    let asked = false
    const check = () => {
      readerApi.earMarks(id).then((answer) => {
        if (cancelled) return
        setGot({ id, marks: earMarkMap(answer) })
        const next = earMarksNext(answer.state, canStart, asked)
        if (next === 'start') {
          asked = true
          readerApi.startEarMarks(id).then(() => !cancelled && check(), () => undefined)
        } else if (next === 'poll') timer = window.setTimeout(check, POLL_MS)
      }, () => undefined)
    }
    check()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [id, canStart])
  return got?.id === id ? got.marks : NO_MARKS
}
