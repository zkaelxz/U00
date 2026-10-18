import { useEffect, useRef, useState } from 'react'

import { ApiError, withSignal } from '../../../../api/client'
import { lineAlternatives, lineGrammar } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import type { LineAlternatives, LineGrammar, ReviewLine } from '../../../../types/review'
import { LineAiPicker } from './LineAiPicker'
import { toApiPick, useLineAiPick } from './lineAiPick'
import { aiUnavailableText } from '../engineReady'
import { EngineGateNote } from '../EngineGateNote'
import { AI_STALE_MESSAGE, suggestionIsStale, type ToolMode } from './reviewLogic'

// Review parity R17-R18: the per-line study tools. "Alternatives" and
// "Grammar" ask an AI engine (on the PC; no key in the browser) and write
// nothing; "Use this" on an alternative goes through the editor's
// compare-and-set patch.

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

// Asks once when opened (keyed by line and mode) and again on "Try again".
// Closing aborts the request; a late answer is ignored.
export function LineTools({ dramaId, line, mode, onClose, onUse }: Props) {
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [using, setUsing] = useState(false)
  const [busy, setBusy] = useState(true)
  // Alternatives and Grammar need engines.paid only for a paid engine.
  const picker = useLineAiPick(dramaId)
  const pickRef = useRef(picker.pick)
  const gated = picker.gate !== null
  // Declared before the request effect, so it holds the latest pick when that runs.
  useEffect(() => {
    pickRef.current = picker.pick
  })

  // Asks once the engine list has answered; a later pick takes effect on "Ask again".
  useEffect(() => {
    if (!picker.ready) return
    // The note under the picker says why; asking would only fail.
    if (gated) {
      setBusy(false)
      return
    }
    setBusy(true)
    const ctl = new AbortController()
    const f = withSignal(ctl.signal)
    const fail = (e: unknown) => {
      if (ctl.signal.aborted) return
      setBusy(false)
      if (e instanceof ApiError && e.status === 503) setUnavailable(true)
      else setError(e)
    }
    const req: Promise<Result> =
      mode === 'alternatives'
        ? lineAlternatives(dramaId, line.id, toApiPick(pickRef.current), f).then((data) => ({ kind: 'alternatives' as const, data }))
        : lineGrammar(dramaId, line.id, toApiPick(pickRef.current), f).then((data) => ({ kind: 'grammar' as const, data }))
    req.then((r) => {
      if (ctl.signal.aborted) return
      setBusy(false)
      setResult(r)
    }, fail)
    return () => ctl.abort()
  }, [dramaId, line.id, mode, attempt, picker.ready, gated])

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
  const title = mode === 'alternatives' ? 'Alternatives' : 'Grammar'
  const hide = `Hide ${title.toLowerCase()}`

  return (
    <div className="review-ai-panel" data-testid="line-tools-panel" aria-label={title} role="group">
      <LineAiPicker picker={picker} disabled={busy || using} />
      <EngineGateNote gate={picker.gate} />
      {busy && <p className="muted">Working…</p>}
      {result?.kind === 'alternatives' && (
        <AlternativeList line={line} data={result.data} busy={using} onUse={use} />
      )}
      {result?.kind === 'grammar' && <GrammarList data={result.data} />}
      {unavailable && (
        <p className="error" role="alert" data-testid="line-tools-unavailable">
          {aiUnavailableText(picker.gate)} <a href="#/settings">Open Settings</a>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="review-actions">
        {(error !== null || unavailable) && !busy && (
          <button type="button" className={buttonClass('secondary', 'sm')} data-testid="line-tools-retry" disabled={picker.gate !== null} onClick={retry}>
            Try again
          </button>
        )}
        {result && !busy && (
          <button type="button" className={buttonClass('secondary', 'sm')} data-testid="line-tools-ask-again" disabled={picker.gate !== null} onClick={retry}>
            Ask again
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
