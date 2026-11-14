// Filling in lines with no speaker: the guesses to offer, and the Undo for the last one taken.
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'

import { undoSpeakerChange } from '../../../../api/characters'
import { listSpeakerSuggestions } from '../../../../api/review'
import type { LineSpeakerGuesses, ReviewLine, SpeakerGuess } from '../../../../types/review'
import { afterLinesWrite, beforeLinesWrite, type SpeakerUndo } from './linesController'
import { needsSpeaker } from './reviewLogic'
import { UndoNotice } from './UndoNotice'

// Speaker guesses for the shown lines that still need one, by line id. Optional:
// a failure (or no permission) just shows none.
export function useSpeakerGuesses(dramaId: number, shown: ReviewLine[], reloads: number, enabled: boolean) {
  const [list, setList] = useState<LineSpeakerGuesses[]>([])
  const ids = enabled ? shown.filter(needsSpeaker).map((l) => l.id).slice(0, 200).join(',') : ''
  useEffect(() => {
    let cancelled = false
    if (!ids) {
      setList([])
      return
    }
    listSpeakerSuggestions(dramaId, ids.split(',').map(Number)).then(
      (r) => !cancelled && setList(r.lines),
      () => !cancelled && setList([]),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, ids, reloads])
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
  const [undo, setUndo] = useState<SpeakerUndo | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!undo || busy) return
    const ids = undo.entries.map((e) => e.id)
    const blocked = await beforeLinesWrite(dramaId, ids)
    if (blocked) {
      setStatus(blocked)
      return
    }
    setBusy(true)
    try {
      await undoSpeakerChange(dramaId, undo.entries)
      afterLinesWrite(dramaId, ids)
      setStatus(`Line #${undo.lineNumber} has no speaker again.`)
      setUndo(null)
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const node = undo ? (
    <UndoNotice message={`Line #${undo.lineNumber} is now ${undo.speaker}.`} busy={busy}
      onUndo={() => void run()} onDismiss={() => setUndo(null)}
      testId="speaker-undo" historyNote={false} className="review-undo-speaker" />
  ) : null
  return { setUndo, node }
}
