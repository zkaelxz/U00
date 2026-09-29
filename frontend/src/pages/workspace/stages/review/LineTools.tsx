import { useEffect, useState } from 'react'

import { ApiError, withSignal } from '../../../../api/client'
import { lineAlternatives, lineGrammar, pronounceLine } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import type { LineAlternatives, LineGrammar, ReviewLine } from '../../../../types/review'
import { AI_STALE_MESSAGE, AI_UNAVAILABLE_MESSAGE, suggestionIsStale, type ToolMode } from './reviewLogic'

// Review parity R17-R19: the per-line study tools. "Alternatives" and
// "Grammar" ask an AI engine (on the PC; no key in the browser) and write
// nothing; "Use this" on an alternative goes through the editor's
// compare-and-set patch. "Pronounce" plays an edge-tts clip of the source.

const PRONOUNCE_UNAVAILABLE = 'Pronouncing needs edge-tts on the PC (see Diagnostics).'

interface Props {
  dramaId: number
  line: ReviewLine
  mode: ToolMode
  onClose: () => void
  // Applies text through the editor's compare-and-set patch; true when saved.
  onUse: (text: string) => Promise<boolean>
}

type Result =
  | { kind: 'alternatives'; data: LineAlternatives }
  | { kind: 'grammar'; data: LineGrammar }
  | { kind: 'pronounce'; url: string }

// Asks once when opened (keyed by line and mode) and again on "Try again".
// Closing aborts the request; a late answer is ignored.
export function LineTools({ dramaId, line, mode, onClose, onUse }: Props) {
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [using, setUsing] = useState(false)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    const ctl = new AbortController()
    const f = withSignal(ctl.signal)
    let url: string | null = null
    const fail = (e: unknown) => {
      if (ctl.signal.aborted) return
      setBusy(false)
      if (e instanceof ApiError && e.status === 503) setUnavailable(true)
      else setError(e)
    }
    const req: Promise<Result | Blob> =
      mode === 'alternatives'
        ? lineAlternatives(dramaId, line.id, f).then((data) => ({ kind: 'alternatives' as const, data }))
        : mode === 'grammar'
          ? lineGrammar(dramaId, line.id, f).then((data) => ({ kind: 'grammar' as const, data }))
          : pronounceLine(dramaId, line.id, f)
    req.then((r) => {
      if (ctl.signal.aborted) return
      setBusy(false)
      if (r instanceof Blob) {
        url = URL.createObjectURL(r)
        setResult({ kind: 'pronounce', url })
      } else setResult(r)
    }, fail)
    return () => {
      ctl.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [dramaId, line.id, mode, attempt])

  const retry = () => {
    setBusy(true)
    setResult(null)
    setError(null)
    setUnavailable(false)
    setAttempt((n) => n + 1)
  }
  const use = (text: string) => {
    setUsing(true)
    onUse(text).then((ok) => ok && onClose()).finally(() => setUsing(false))
  }
  const title = mode === 'alternatives' ? 'Alternatives' : mode === 'grammar' ? 'Grammar' : 'Pronounce'
  const hide = `Hide ${title.toLowerCase()}`

  return (
    <div className="review-ai-panel" data-testid="line-tools-panel" aria-label={title} role="group">
      {busy && <p className="muted">{mode === 'pronounce' ? 'Making the audio…' : 'Working…'}</p>}
      {result?.kind === 'alternatives' && (
        <AlternativeList line={line} data={result.data} busy={using} onUse={use} />
      )}
      {result?.kind === 'grammar' && <GrammarList data={result.data} />}
      {result?.kind === 'pronounce' && (
        <audio controls autoPlay src={result.url} data-testid="pronounce-audio" className="review-pronounce">
          <track kind="captions" />
        </audio>
      )}
      {unavailable && (
        <p className="error" role="alert" data-testid="line-tools-unavailable">
          {mode === 'pronounce' ? (
            PRONOUNCE_UNAVAILABLE
          ) : (
            <>
              {AI_UNAVAILABLE_MESSAGE} <a href="#/settings">Open Settings</a>
            </>
          )}
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="review-actions">
        {(error !== null || unavailable || mode === 'pronounce') && !busy && (
          <button type="button" className={buttonClass('secondary', 'sm')} data-testid="line-tools-retry" onClick={retry}>
            {mode === 'pronounce' && result ? 'Make again' : 'Try again'}
          </button>
        )}
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>
          {hide}
        </button>
      </div>
    </div>
  )
}

function AlternativeList({ line, data, busy, onUse }: {
  line: ReviewLine
  data: LineAlternatives
  busy: boolean
  onUse: (text: string) => void
}) {
  const stale = suggestionIsStale(line, data.current_en)
  return (
    <>
      {stale && <p className="error" role="alert">{AI_STALE_MESSAGE}</p>}
      <ol className="review-alts" data-testid="line-alternatives">
        {data.alternatives.map((a, i) => (
          <li key={i}>
            <div className="review-ai-text">{a.translation}</div>
            {(a.approach || a.tradeoff) && (
              <p className="muted">
                {a.approach}
                {a.tradeoff && ` · trades away: ${a.tradeoff}`}
              </p>
            )}
            {a.translation !== line.en && (
              <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || stale} onClick={() => onUse(a.translation)}>
                Use this
              </button>
            )}
          </li>
        ))}
      </ol>
    </>
  )
}

// A list, not a table, so it wraps on a phone instead of scrolling sideways.
function GrammarList({ data }: { data: LineGrammar }) {
  return (
    <ul className="review-grammar" data-testid="line-grammar">
      {data.parts.map((p, i) => (
        <li key={i}>
          <strong lang="zh">{p.word}</strong>
          {p.reading && <span className="muted"> {p.reading}</span>}
          {p.meaning && <> · {p.meaning}</>}
          {p.function && <span className="muted"> ({p.function})</span>}
        </li>
      ))}
    </ul>
  )
}
