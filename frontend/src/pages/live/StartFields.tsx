/* The two fields every start needs, on a phone-sized card: the stream link with a Paste button, and the language as one tap among a few. */
import { useState } from 'react'

import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'

type StreamLinkProps = { value: string; maxLength: number; disabled: boolean; onChange: (v: string) => void }

export function StreamLinkField({ value, maxLength, disabled, onChange }: StreamLinkProps) {
  const [pasteFailed, setPasteFailed] = useState(false)
  // Reading the clipboard needs a secure page and the viewer's permission; without either the button is not offered.
  const canPaste = typeof navigator !== 'undefined' && typeof navigator.clipboard?.readText === 'function'
  const paste = async () => {
    setPasteFailed(false)
    try {
      const text = (await navigator.clipboard.readText()).trim()
      if (text) onChange(text.slice(0, maxLength))
      else setPasteFailed(true)
    } catch {
      setPasteFailed(true)
    }
  }
  return (
    <Field label="Stream link" help="A live stream or video page (YouTube, Bilibili, …) that yt-dlp can open. Signed-in (cookie) streams don't work from here.">
      <LinkInput value={value} maxLength={maxLength} disabled={disabled} onChange={onChange}
        onPaste={canPaste ? () => void paste() : undefined} pasteFailed={pasteFailed} />
    </Field>
  )
}

type LanguageProps = { value: string; options: { code: string; label: string }[]; disabled: boolean; onChange: (code: string) => void }

export function LanguagePicker({ value, options, disabled, onChange }: LanguageProps) {
  return (
    <Field label="Language">
      <div className="live-langs" role="radiogroup" aria-label="Language">
        {options.map((l) => (
          <button key={l.code} type="button" role="radio" aria-checked={value === l.code} disabled={disabled}
            className={buttonClass(value === l.code ? 'primary' : 'secondary')} onClick={() => onChange(l.code)}>
            {l.label}
          </button>
        ))}
      </div>
    </Field>
  )
}

type LinkInputProps = StreamLinkProps & { onPaste?: () => void; pasteFailed: boolean; id?: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }

// Field hands its id and aria props to its one child; they belong on the input, not on the wrapper.
function LinkInput({ value, maxLength, disabled, onChange, onPaste, pasteFailed, id, 'aria-describedby': describedBy, 'aria-invalid': invalid }: LinkInputProps) {
  return (
    <div className="live-link-field">
      <div className="live-link-row">
        <input id={id} aria-describedby={describedBy} aria-invalid={invalid} type="url" inputMode="url" autoComplete="off" spellCheck={false}
          maxLength={maxLength} placeholder="https://www.youtube.com/watch?v=…" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
        {onPaste && <button type="button" className={buttonClass('secondary')} disabled={disabled} onClick={onPaste}>Paste</button>}
      </div>
      {pasteFailed && <p className="muted" role="status">Nothing to paste. Copy the stream link first, and allow pasting if asked.</p>}
    </div>
  )
}
