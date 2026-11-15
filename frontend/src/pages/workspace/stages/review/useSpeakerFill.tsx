// Filling in lines with no speaker: the guesses to offer, and the Undo for the last one taken.
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'

import { undoSpeakerChange } from '../../../../api/characters'
import { listSpeakerSuggestions } from '../../../../api/review'
import type { LineSpeakerGuesses, ReviewLine, SpeakerGuess } from '../../../../types/review'
import { afterLinesWrite, beforeLinesWrite, type SpeakerUndo } from './linesController'
import { needsSpeaker } from './reviewLogic'
import { UndoNotice } from './UndoNotice'

// Speaker guesses for the shown lines that still need one, by line id. Optional:
// a failure (or no permission) just shows none.
const EMPTY: LineSpeakerGuesses[] = []

export function useSpeakerGuesses(dramaId: number, shown: ReviewLine[], reloads: number, enabled: boolean) {
  const [got, setGot] = useState<{ dramaId: number; lines: LineSpeakerGuesses[] }>({ dramaId, lines: [] })
  const ids = enabled ? shown.filter(needsSpeaker).map((l) => l.id).slice(0, 200).join(',') : ''
  useEffect(() => {
    if (!ids) return
    let cancelled = false
    listSpeakerSuggestions(dramaId, ids.split(',').map(Number)).then(
      (r) => !cancelled && setGot({ dramaId, lines: r.lines }),
      () => !cancelled && setGot({ dramaId, lines: [] }),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, ids, reloads])
  // The last answer stays while the next one loads, so the other rows' buttons don't blink
  // after each pick; a row that no longer needs a speaker hides its own.
  const list = ids && got.dramaId === dramaId ? got.lines : EMPTY
  return useMemo(() => new Map<number, SpeakerGuess[]>(list.map((g) => [g.line_id, g.speakers])), [list])
}

// The Undo for the last suggested speaker taken: it puts the line back to no speaker while
// the line still has the speaker it was given.
export function useSpeakerUndo(
  dramaId: number,
  onChanged: () => void,
  setStatus: Dispatch<SetStateAction<string | null>>,
  setError: Dispatch<SetStateAction<unknown>>,
) {
  // Kept with its title, so an offer never outlives a switch to another title.
  const [held, setHeld] = useState<{ dramaId: number; undo: SpeakerUndo } | null>(null)
  const undo = held?.dramaId === dramaId ? held.undo : null
  const setUndo = (u: SpeakerUndo | null) => setHeld(u && { dramaId, undo: u })
  const [busy, setBusy] = useState(false)
  // busy reaches this closure only after a render, so a fast second click would post the undo again.
  const running = useRef(false)
  const run = async () => {
    if (!undo || running.current) return
    running.current = true
    setBusy(true)
    const ids = undo.entries.map((e) => e.id)
    try {
      const blocked = await beforeLinesWrite(dramaId, ids)
      if (blocked) {
        setStatus(blocked)
        return
      }
      await undoSpeakerChange(dramaId, undo.entries)
      afterLinesWrite(dramaId, ids)
      setStatus(`Line #${undo.lineNumber} has no speaker again.`)
      setUndo(null)
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      running.current = false
      setBusy(false)
    }
  }
  const node = undo ? (
    <UndoNotice message={`Line #${undo.lineNumber} is now ${undo.speaker}.`} busy={busy}
      onUndo={() => void run()} onDismiss={() => setUndo(null)}
      testId="speaker-undo" historyNote={false} className="review-undo-speaker" />
  ) : null
  // The note belongs to the one line the last pick set; it goes with the Undo when that is used or dismissed.
  const noteLineId = undo?.note ? undo.entries[0]?.id : undefined
  return { setUndo, node, noteLineId, note: undo?.note ?? null }
}

// Everything the panel needs for blank-speaker lines: the guesses (for the rows and the 1/2/3 keys),
// how many lines still need a speaker (0 where the step button is hidden) and the Undo for a taken one.
export function useSpeakerFill(
  dramaId: number,
  count: number,
  shown: ReviewLine[],
  reloads: number,
  active: ReviewLine | null,
  prose: boolean,
  onChanged: () => void,
  setStatus: Dispatch<SetStateAction<string | null>>,
  setError: Dispatch<SetStateAction<unknown>>,
) {
  const undo = useSpeakerUndo(dramaId, onChanged, setStatus, setError)
  const guessesByLine = useSpeakerGuesses(dramaId, shown, reloads, !prose && count > 0)
  const activeGuesses = (active && needsSpeaker(active) ? guessesByLine.get(active.id) : null) ?? []
  const noteFor = (id: number) => (id === undo.noteLineId ? undo.note : null)
  return { setUndo: undo.setUndo, undoNode: undo.node, stepCount: prose ? 0 : count, guessesByLine, activeGuesses, noteFor }
}
