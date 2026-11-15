/** The speaker's name on a line as a button: press it, pick another speaker (or type a new one), and it is saved at once. */
import { useEffect, useId, useRef, useState } from 'react'

import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine } from '../../../../types/review'
import type { RowActions } from './LineRow'
import { speakerOptions } from './speakerOptions'
import { useTitleSpeakers } from './useTitleSpeakers'

export function SpeakerChip({ dramaId, line, actions }: { dramaId: number; line: ReviewLine; actions: RowActions }) {
  const [open, setOpen] = useState(false)
  const [typing, setTyping] = useState('')
  const [busy, setBusy] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const listId = useId()

  // Read when the list is opened, not for every row on the page.
  const known = useTitleSpeakers(dramaId, open)

  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => !root.current?.contains(e.target as Node) && setOpen(false)
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])

  const pick = async (speaker: string) => {
    const name = speaker.trim()
    if (!name || busy) return
    if (name === line.speaker) {
      setOpen(false)
      return
    }
    setBusy(true)
    const ok = await actions.acceptSpeaker(line.id, name)
    setBusy(false)
    if (ok) {
      setOpen(false)
      setTyping('')
    }
  }

  const options = known ? speakerOptions(known, line.speaker ?? '') : []
  return (
    <span className="review-speaker-pick" ref={root} onKeyDown={(e) => e.key === 'Escape' && open && (e.stopPropagation(), setOpen(false))}>
      <button type="button" className="review-speaker" aria-expanded={open} aria-controls={open ? listId : undefined}
              aria-label={`Speaker of line ${lineNumber(line.idx)}: ${line.speaker}. Change it`} title="Change the speaker" onClick={() => setOpen(!open)}>
        {line.speaker}
      </button>
      {open && (
        <div className="review-speaker-list" id={listId} role="group" aria-label="Choose a speaker">
          {known === null && <span className="muted">Loading…</span>}
          {options.map((s) => (
            <button key={s} type="button" className={buttonClass('ghost', 'sm')} aria-pressed={s === line.speaker} disabled={busy} onClick={() => void pick(s)}>
              {s}
            </button>
          ))}
          <form className="review-speaker-new" onSubmit={(e) => (e.preventDefault(), void pick(typing))}>
            <input aria-label="New speaker" placeholder="New speaker" value={typing} onChange={(e) => setTyping(e.target.value)} />
            <button type="submit" className={buttonClass('secondary', 'sm')} disabled={busy || !typing.trim()}>Set</button>
          </form>
        </div>
      )}
    </span>
  )
}
