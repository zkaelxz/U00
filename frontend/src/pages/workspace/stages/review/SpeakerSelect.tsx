// Speaker picker for the line edit form: the title's existing speakers, plus a way to type a new one.
import { useEffect, useState } from 'react'

import { getJson } from '../../../../api/client'
import { speakerOptions } from './speakerOptions'
import type { components } from '../../../../api/generated/schema'

type SpeakerEntry = components['schemas']['CharactersEntry']

const NEW_SPEAKER = '\u0000new'

interface Props {
  // Field hands its label, description and error wiring to the control it wraps through these.
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
  dramaId: number
  value: string
  onChange: (speaker: string) => void
}

export function SpeakerSelect({ dramaId, value, onChange, ...fieldProps }: Props) {
  // null while loading or after a failed load: the plain text box below keeps the field usable either way.
  const [known, setKnown] = useState<string[] | null>(null)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    let cancelled = false
    getJson<SpeakerEntry[]>(`/api/characters/dramas/${dramaId}`).then(
      (rows) => !cancelled && setKnown(rows.map((r) => r.speaker_label).filter(Boolean)),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (known === null || adding) {
    return <input {...fieldProps} value={value} autoFocus={adding} onChange={(e) => onChange(e.target.value)} />
  }

  const options = speakerOptions(known, value)
  return (
    <select
      {...fieldProps}
      value={value}
      onChange={(e) => (e.target.value === NEW_SPEAKER ? (setAdding(true), onChange('')) : onChange(e.target.value))}
    >
      <option value="">No speaker</option>
      {options.map((s) => (
        <option key={s} value={s}>{s}</option>
      ))}
      <option value={NEW_SPEAKER}>Add new speaker…</option>
    </select>
  )
}
