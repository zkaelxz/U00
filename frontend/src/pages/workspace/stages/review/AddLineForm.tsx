import { useState } from 'react'

import { Field } from '../../../../components/Field'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import { gapForNewLine } from './reviewLogic'
import { lineNumber } from '../../../../lineNumber'

export interface NewLine {
  start: number
  end: number
  zh: string
  en: string
  speaker: string | null
}

interface Props {
  // null = the new line goes first.
  after: ReviewLine | null
  next: ReviewLine | null
  busy: boolean
  blocked: string | null
  onAdd: (line: NewLine) => void
  // Adds the line with a blank source and transcribes its audio window at once;
  // omitted when the title has no audio to hear.
  onAddAndTranscribe?: (line: NewLine) => void
  onCancel?: () => void
}

// Insert a new line. Start/End come pre-filled from the gap after `after`.
export function AddLineForm({ after, next, busy, blocked, onAdd, onAddAndTranscribe, onCancel }: Props) {
  const gap = gapForNewLine(after, next)
  const [start, setStart] = useState(String(gap.start))
  const [end, setEnd] = useState(String(gap.end))
  const [speaker, setSpeaker] = useState(after?.speaker ?? '')
  const [zh, setZh] = useState('')
  const [en, setEn] = useState('')
  const s = Number(start)
  const e = Number(end)
  const problem =
    start.trim() === '' || end.trim() === '' || Number.isNaN(s) || Number.isNaN(e) || s < 0
      ? 'Enter start and end in seconds.'
      : e <= s
        ? 'End must be after start.'
        : null

  return (
    <form
      className="review-form"
      onSubmit={(ev) => {
        ev.preventDefault()
        if (problem || busy || blocked) return
        onAdd({ start: s, end: e, zh, en, speaker: speaker.trim() || null })
      }}
    >
      <div className="review-edit-row">
        <Field label="Start (s)">
          <input inputMode="decimal" value={start} onChange={(ev) => setStart(ev.target.value)} />
        </Field>
        <Field label="End (s)">
          <input inputMode="decimal" value={end} onChange={(ev) => setEnd(ev.target.value)} />
        </Field>
        <Field label="Speaker">
          <input value={speaker} onChange={(ev) => setSpeaker(ev.target.value)} />
        </Field>
      </div>
      <Field label="Source">
        <textarea lang="zh" rows={2} value={zh} onChange={(ev) => setZh(ev.target.value)} />
      </Field>
      <Field label="Translation">
        <textarea rows={2} value={en} onChange={(ev) => setEn(ev.target.value)} />
      </Field>
      {problem && <p className="error" role="alert">{problem}</p>}
      {blocked && <p className="muted">{blocked}</p>}
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={!!problem || busy || !!blocked}>
          {busy ? 'Adding…' : after ? `Add after #${lineNumber(after.idx)}` : 'Add first line'}
        </button>
        {onAddAndTranscribe && (
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={!!problem || busy || !!blocked || zh.trim() !== ''}
            title={zh.trim() !== '' ? 'Leave the source empty to have it transcribed from the audio.' : undefined}
            onClick={() => onAddAndTranscribe({ start: s, end: e, zh: '', en, speaker: speaker.trim() || null })}
          >
            Add and transcribe
          </button>
        )}
        {onCancel && <button type="button" className={buttonClass('ghost')} onClick={onCancel}>Back</button>}
      </div>
    </form>
  )
}
