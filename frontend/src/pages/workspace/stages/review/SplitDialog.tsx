import { useId, useState, type SyntheticEvent } from 'react'

import { Field } from '../../../../components/Field'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import { charCount, codePointOffset, estimateSplitTime, proportionalCut, splitPieces, stepToBoundary } from './reviewLogic'
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
  // Plays the audio between two times; absent without media.
  onPlayRange?: (start: number, end: number) => void
}

const clampInside = (n: number, len: number) => Math.min(Math.max(1, n), Math.max(1, len - 1))

const PLAY_AROUND_S = 1.5
const fmt = (n: number) => `${Math.round(n * 100) / 100}s`

interface CutTextProps {
  label: string
  text: string
  lang?: string
  at: number
  onChange: (n: number) => void
}

// The text drawn as two shaded pieces with a bar where it breaks. Tapping a
// character moves the cut; the buttons nudge it for fingers and keyboards. The
// read-only textarea stays for screen readers and caret taps, and the visual is
// hidden from them in favour of a live read-out of the two pieces.
function CutText({ label, text, lang, at, onChange }: CutTextProps) {
  const textId = useId()
  const len = charCount(text)
  const chars = Array.from(text)
  const move = (n: number) => onChange(clampInside(n, len))
  const fromCaret = (e: SyntheticEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget
    const n = codePointOffset(el.value, el.selectionStart ?? 0)
    if (n > 0 && n < len) onChange(n)
  }
  const [first, second] = splitPieces(text, at)
  return (
    <div className="field-item">
      <div className="field-label-row"><label htmlFor={textId}>{label}</label></div>
      <div className="split-cut">
        <div className="split-cut-text" lang={lang} aria-hidden="true">
          {chars.map((c, i) => (
            <span key={i}>
              {i === at && <span className="split-cut-mark" />}
              <span
                className={i < at ? 'split-cut-a' : 'split-cut-b'}
                onClick={(e) => {
                  const box = e.currentTarget.getBoundingClientRect()
                  move(e.clientX > box.left + box.width / 2 ? i + 1 : i)
                }}
              >
                {c}
              </span>
            </span>
          ))}
        </div>
        <div className="split-cut-nudge">
          <button type="button" onClick={() => move(stepToBoundary(text, at, -1))} disabled={at <= 1} aria-label={`${label}: previous punctuation or space`}>⇤</button>
          <button type="button" onClick={() => move(at - 1)} disabled={at <= 1} aria-label={`${label}: cut 1 char earlier`}>−1 char</button>
          <button type="button" onClick={() => move(at + 1)} disabled={at >= len - 1} aria-label={`${label}: cut 1 char later`}>+1 char</button>
          <button type="button" onClick={() => move(stepToBoundary(text, at, 1))} disabled={at >= len - 1} aria-label={`${label}: next punctuation or space`}>⇥</button>
        </div>
        <textarea id={textId} lang={lang} rows={2} value={text} readOnly onSelect={fromCaret} />
        <span className="visually-hidden" aria-live="polite">First piece: {first}. Second piece: {second}.</span>
      </div>
    </div>
  )
}

// Split one line in two. The cursor in the source text (or the number field)
// sets where it breaks; the translation stays on the first piece unless it is
// split too. Nothing changes until "Split line".
export function SplitDialog({ line, initialAt, initialEnAt, busy, blocked, onSplit, onCancel, onPlayRange }: Props) {
  const zhLen = charCount(line.zh)
  const enLen = charCount(line.en)
  const [at, setAt] = useState(() => clampInside(initialAt ?? Math.round(zhLen / 2), zhLen))
  const [splitEn, setSplitEn] = useState(initialEnAt !== undefined && initialEnAt !== null && enLen > 1)
  // Null until the user picks a translation cut: it then follows the source cut.
  const [enManual, setEnManual] = useState<number | null>(initialEnAt ?? null)
  const [time, setTime] = useState('')
  const enAt = clampInside(enManual ?? proportionalCut(zhLen, at, line.en), enLen)

  const [zh1, zh2] = splitPieces(line.zh, at)
  const [en1, en2] = splitEn ? splitPieces(line.en, enAt) : [line.en, '']
  const estimate = estimateSplitTime(line, at)
  const timeNum = time.trim() === '' ? null : Number(time)
  const timeBad = timeNum !== null && (Number.isNaN(timeNum) || timeNum <= line.start || timeNum >= line.end)
  const splitTime = timeNum !== null && !timeBad ? timeNum : estimate
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
      <p className="muted review-hint-text">Tap where the line should break, use the nudge buttons, or set the character count.</p>
      <CutText label="Source" text={line.zh} lang="zh" at={at} onChange={setAt} />
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
        <Field label="Split at (s)" error={timeBad ? `Enter a time between ${line.start} and ${line.end}.` : null}>
          <input inputMode="decimal" placeholder={`Blank ≈ ${estimate} s`} value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
      </div>
      <p className="muted review-hint-text">Estimated from text length; use Play to find the real point. Blank uses the estimate.</p>
      {onPlayRange && (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('ghost')}
            onClick={() => onPlayRange(Math.max(line.start, splitTime - PLAY_AROUND_S), Math.min(line.end, splitTime + PLAY_AROUND_S))}
          >
            ▶ Play around the cut
          </button>
        </div>
      )}
      {enLen > 1 && (
        <div className="setting-list review-toggles">
          <Field label="Also split the translation">
            <Toggle checked={splitEn} onChange={setSplitEn} />
          </Field>
        </div>
      )}
      {splitEn && <CutText label="Translation" text={line.en} at={enAt} onChange={setEnManual} />}
      {splitEn && enManual === null && <p className="muted review-hint-text">Suggested from the source cut; tap to change.</p>}
      <div className="review-preview" data-testid="split-preview">
        <div>
          <strong>Line stays</strong> <span className="muted">#{lineNumber(line.idx)} · {fmt(line.start)}–{fmt(splitTime)}</span>
          <div>{zh1} <span className="muted">·</span> {en1 || <span className="muted">(no translation)</span>}</div>
        </div>
        <div>
          <strong>New line</strong> <span className="muted">{fmt(splitTime)}–{fmt(line.end)}</span>
          <div>{zh2} <span className="muted">·</span> {en2 || <span className="muted">(no translation)</span>}</div>
        </div>
        {!splitEn && enLen > 0 && (
          <p className="split-note" data-testid="split-no-en-note">Translation stays on the first line; the new line will have none.</p>
        )}
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
