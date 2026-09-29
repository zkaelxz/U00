import { useState, type SyntheticEvent } from 'react'

import { Field } from '../../../../components/Field'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import { charCount, codePointOffset, estimateSplitTime, splitPieces } from './reviewLogic'
import { lineNumber } from '../../../../lineNumber'

export interface SplitChoice {
  at_char: number
  en_at_char: number | null
  at_time: number | null
}

interface Props {
  line: ReviewLine
  // Code-point offsets to start from (Alt+Enter passes the editor's cursor).
  initialAt?: number
  initialEnAt?: number | null
  busy: boolean
  blocked: string | null
  onSplit: (choice: SplitChoice) => void
  onCancel: () => void
}

const clampInside = (n: number, len: number) => Math.min(Math.max(1, n), Math.max(1, len - 1))

// Split one line in two. The cursor in the source text (or the number field)
// sets where it breaks; the translation stays on the first piece unless it is
// split too. Nothing changes until "Split line".
export function SplitDialog({ line, initialAt, initialEnAt, busy, blocked, onSplit, onCancel }: Props) {
  const zhLen = charCount(line.zh)
  const enLen = charCount(line.en)
  const [at, setAt] = useState(() => clampInside(initialAt ?? Math.round(zhLen / 2), zhLen))
  const [splitEn, setSplitEn] = useState(initialEnAt !== undefined && initialEnAt !== null && enLen > 1)
  const [enAt, setEnAt] = useState(() => clampInside(initialEnAt ?? Math.round(enLen / 2), enLen))
  const [time, setTime] = useState('')

  const fromCaret = (setter: (n: number) => void, len: number) => (e: SyntheticEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget
    const n = codePointOffset(el.value, el.selectionStart ?? 0)
    if (n > 0 && n < len) setter(n)
  }

  const [zh1, zh2] = splitPieces(line.zh, at)
  const [en1, en2] = splitEn ? splitPieces(line.en, enAt) : [line.en, '']
  const estimate = estimateSplitTime(line, at)
  const timeNum = time.trim() === '' ? null : Number(time)
  const timeBad = timeNum !== null && (Number.isNaN(timeNum) || timeNum <= line.start || timeNum >= line.end)
  const valid = zhLen >= 2 && at > 0 && at < zhLen && !timeBad && (!splitEn || (enAt > 0 && enAt < enLen))

  if (zhLen < 2) {
    return (
      <div className="review-form">
        <p className="muted">This line’s source text is too short to split.</p>
        <div className="actions"><button type="button" onClick={onCancel}>Back</button></div>
      </div>
    )
  }

  return (
    <form
      className="review-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid && !busy && !blocked) onSplit({ at_char: at, en_at_char: splitEn ? enAt : null, at_time: timeNum })
      }}
    >
      <p className="muted review-hint-text">Tap where the line should break, or set the character count.</p>
      <Field label="Source">
        <textarea
          lang="zh"
          rows={2}
          value={line.zh}
          readOnly
          onSelect={fromCaret(setAt, zhLen)}
        />
      </Field>
      <div className="review-edit-row">
        <Field label="Break after (chars)">
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={zhLen - 1}
            value={at}
            onChange={(e) => setAt(clampInside(Number(e.target.value) || 1, zhLen))}
          />
        </Field>
        <Field label="Split at (s)" help="Blank splits the timing in proportion to the text." error={timeBad ? `Enter a time between ${line.start} and ${line.end}.` : null}>
          <input inputMode="decimal" placeholder={`blank ≈ ${estimate} s`} value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
      </div>
      {enLen > 1 && (
        <div className="setting-list review-toggles">
          <Field label="Also split the translation">
            <Toggle checked={splitEn} onChange={setSplitEn} />
          </Field>
        </div>
      )}
      {splitEn && (
        <Field label="Translation" help="Tap where the translation should break.">
          <textarea rows={2} value={line.en} readOnly onSelect={fromCaret(setEnAt, enLen)} />
        </Field>
      )}
      <div className="review-preview" data-testid="split-preview">
        <div><span className="muted">#{lineNumber(line.idx)}</span> {zh1} <span className="muted">·</span> {en1 || <span className="muted">(no translation)</span>}</div>
        <div><span className="muted">new</span> {zh2} <span className="muted">·</span> {en2 || <span className="muted">(no translation)</span>}</div>
      </div>
      {blocked && <p className="muted">{blocked}</p>}
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={!valid || busy || !!blocked}>
          {busy ? 'Splitting…' : 'Split line'}
        </button>
        <button type="button" className={buttonClass('ghost')} onClick={onCancel}>Back</button>
      </div>
    </form>
  )
}
