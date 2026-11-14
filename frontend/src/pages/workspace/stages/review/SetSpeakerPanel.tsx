import { useEffect, useRef, useState } from 'react'

import { setLineSpeaker, undoSpeakerChange } from '../../../../api/characters'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { buttonClass } from '../../../../components/uiClasses'
import type { SpeakerChangeEntry } from '../../../../types/characters'
import { SpeakerSelect } from './SpeakerSelect'

interface Props {
  dramaId: number
  lineIds: number[]
  // Non-zero once for each press of the selection bar's button.
  scrollSignal?: number
  onScrolled?: () => void
  onChanged: () => void
  onClose: () => void
}

const lines = (n: number) => `${n} ${n === 1 ? 'line' : 'lines'}`

// Opened from the selection bar's "Set speaker…": one speaker for every ticked
// line, with an Undo that only works while those lines are as it left them.
export function SetSpeakerPanel({ dramaId, lineIds, scrollSignal, onScrolled, onChanged, onClose }: Props) {
  // The panel sits above the list, so with lines ticked further down it would
  // open out of sight and the button would seem to do nothing.
  const panelRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!scrollSignal) return
    panelRef.current?.scrollIntoView?.({ block: 'center' })
    onScrolled?.()
  }, [scrollSignal, onScrolled])
  const [speaker, setSpeaker] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<{ text: string; undo: SpeakerChangeEntry[] } | null>(null)

  const apply = () => {
    setBusy(true)
    setLineSpeaker(dramaId, lineIds, speaker.trim()).then(
      (r) => {
        setBusy(false)
        setError(null)
        setDone({ text: `Set the speaker of ${lines(r.updated)} to ${speaker.trim()}.`, undo: r.undo })
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  const undo = () => {
    if (!done) return
    setBusy(true)
    undoSpeakerChange(dramaId, done.undo).then(
      (r) => {
        setBusy(false)
        setError(null)
        setDone({ text: `Put the old speaker back on ${lines(r.updated)}.`, undo: [] })
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <section ref={panelRef} className="review-set-speaker" aria-label="Set speaker" data-testid="set-speaker">
      <form onSubmit={(e) => { e.preventDefault(); apply() }}>
        <Field label={`Speaker for ${lines(lineIds.length)}`}>
          <SpeakerSelect dramaId={dramaId} value={speaker} onChange={setSpeaker} />
        </Field>
        <button type="submit" className={buttonClass('primary', 'sm')} disabled={busy || !speaker.trim()}
          title={speaker.trim() ? undefined : 'Choose a speaker first.'}>
          Set speaker
        </button>
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={onClose}>Close</button>
      </form>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {done && (
        <p role="status">
          {done.text}
          {done.undo.length > 0 && <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={undo}>Undo</button>}
        </p>
      )}
    </section>
  )
}
