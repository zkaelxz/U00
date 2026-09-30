import { useEffect, useRef, useState } from 'react'

import { LOG_KEYWORD_MAX, getLog } from '../../api/diagnostics'
import { copyText } from '../../components/clipboard'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { usePersistedState } from '../../hooks/usePersistedState'
import {
  COPIED_MS, LOG_DEBOUNCE_MS, LOG_LINE_CHOICES, copyFallbackText, logEmptyText, useDetailsOpen,
} from './diagnosticsAdmin'

/** "Log": the last 50/100/200 redacted log lines, loaded when opened. */
export function LogSection() {
  const [openRef, open] = useDetailsOpen()
  const [stored, setN] = usePersistedState<number>('diagnostics.logN', 50)
  const n = (LOG_LINE_CHOICES as readonly number[]).includes(stored) ? stored : 50
  const [text, setText] = useState('')
  const [keyword, setKeyword] = useState('')
  const [tick, setTick] = useState(0)
  const [lines, setLines] = useState<string[] | null>(null)
  // The request the shown lines answer; "loading" is derived from it.
  const [loadedKey, setLoadedKey] = useState('')
  const [error, setError] = useState<unknown>(null)

  // Typing settles for 300 ms before the filter applies; Enter applies it now.
  useEffect(() => {
    if (text === keyword) return
    const t = setTimeout(() => setKeyword(text), LOG_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [text, keyword])

  const requestKey = `${n}|${keyword}|${tick}`
  const loading = open && loadedKey !== requestKey
  useEffect(() => {
    if (!open) return
    let live = true
    getLog(n, keyword).then(
      (r) => {
        if (!live) return
        setLines(r.lines)
        setError(null)
        setLoadedKey(`${n}|${keyword}|${tick}`)
      },
      (e: unknown) => {
        if (!live) return
        setError(e)
        setLoadedKey(`${n}|${keyword}|${tick}`)
      },
    )
    return () => {
      live = false
    }
  }, [open, n, keyword, tick])

  return (
    <Section title="Log" storageKey="diagnostics.log" summary="Recent lines from the app log">
      <div ref={openRef} className="diag-stack">
        <div className="field-row">
          <Field label="Filter" help="A drama title, or ERROR.">
            <input
              type="search"
              value={text}
              maxLength={LOG_KEYWORD_MAX}
              autoComplete="off"
              enterKeyHint="search"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return
                e.preventDefault()
                setKeyword(text)
                setTick((t) => t + 1)
              }}
            />
          </Field>
          <Field label="Lines">
            <select value={n} onChange={(e) => setN(Number(e.target.value))}>
              {LOG_LINE_CHOICES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={loading} onClick={() => setTick((t) => t + 1)}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {lines !== null &&
          (lines.length === 0 ? (
            <p className="muted" data-testid="log-empty">{logEmptyText(keyword)}</p>
          ) : (
            <CopyBlock text={lines.join('\n')} label="Log lines" />
          ))}
      </div>
    </Section>
  )
}

/**
 * A <pre> with a Copy button. Without the clipboard API (plain http on
 * another device, or a refusal), the text is selected and the person is
 * told how to copy it themselves.
 */
export function CopyBlock({ text, label }: { text: string; label: string }) {
  const preRef = useRef<HTMLPreElement>(null)
  const touch = useMediaQuery('(pointer: coarse)')
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!note) return
    const t = setTimeout(() => setNote(''), COPIED_MS)
    return () => clearTimeout(t)
  }, [note])

  const selectAll = () => {
    const pre = preRef.current
    const sel = window.getSelection()
    if (!pre || !sel) return
    const range = document.createRange()
    range.selectNodeContents(pre)
    sel.removeAllRanges()
    sel.addRange(range)
  }

  const copy = async () => {
    if (await copyText(text)) {
      setNote('Copied.')
    } else {
      selectAll()
      setNote(copyFallbackText(touch))
    }
  }

  return (
    <div className="diag-stack">
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void copy()}>
          Copy
        </button>
        <span className="muted" aria-live="polite">
          {note}
        </span>
      </div>
      <pre ref={preRef} className="diag-pre" aria-label={label} tabIndex={0}>
        {text}
      </pre>
    </div>
  )
}
