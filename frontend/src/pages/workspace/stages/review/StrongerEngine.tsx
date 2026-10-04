import { useEffect, useRef, useState } from 'react'

import { ApiError, withSignal } from '../../../../api/client'
import { tryStrongerEngine } from '../../../../api/strongerEngine'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import type { StrongerLineResult } from '../../../../types/strongerEngine'
import { formatUsd, resultIsStale, STALE_MESSAGE, tryErrorText, tryLabel, type StrongerOffer } from './strongerEngineLogic'
import './strongerEngine.css'

interface Props {
  dramaId: number
  line: ReviewLine
  offer: StrongerOffer
  // Idle, the offer shows on the active row only (next to its AI actions); a
  // running try or a result stays until it is used or dismissed.
  active: boolean
  // Applies text through the editor's compare-and-set patch; true when saved.
  onUse: (text: string) => Promise<boolean>
}

// On a hard line, offer the stronger engine picked in Settings for
// this one line. Suggest only: nothing runs without a click, the try writes
// nothing, and the result is applied only by "Use this".
export function StrongerEngine({ dramaId, line, offer, active, onUse }: Props) {
  const [busy, setBusy] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<StrongerLineResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  // Aborts the in-flight try on Cancel or when the row goes away; a late
  // answer is then ignored.
  const abortRef = useRef<AbortController | null>(null)
  useEffect(() => () => abortRef.current?.abort(), [])

  if (!active && !busy && !result && error === null) return null

  const run = () => {
    if (abortRef.current) return
    const ctl = new AbortController()
    abortRef.current = ctl
    setBusy(true)
    setResult(null)
    setError(null)
    tryStrongerEngine(dramaId, line.id, withSignal(ctl.signal))
      .then(
        (r) => !ctl.signal.aborted && setResult(r),
        (e) => !ctl.signal.aborted && setError(e),
      )
      .finally(() => {
        if (abortRef.current === ctl) abortRef.current = null
        if (!ctl.signal.aborted) setBusy(false)
      })
  }
  const cancel = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
  }
  const use = (text: string) => {
    setApplying(true)
    onUse(text)
      .then((ok) => ok && setResult(null))
      .finally(() => setApplying(false))
  }

  const stale = result ? resultIsStale(line.en, result.based_on_en) : false
  const same = result ? result.text === (line.en ?? '') : false
  const errorText = error !== null ? tryErrorText(error, offer.engineLabel) : null

  return (
    <div className="review-stronger" data-testid="line-stronger">
      {!result && (
        <div className="review-stronger-offer">
          {offer.caption && <span className="muted" data-testid="line-stronger-reasons">{offer.caption}</span>}
          <span className="review-actions">
            <button
              type="button"
              className={buttonClass('secondary', 'sm')}
              disabled={busy}
              aria-busy={busy || undefined}
              title="One line, one paid call. Nothing changes until you choose Use this."
              onClick={run}
            >
              {busy ? `Translating with ${offer.engineLabel}…` : tryLabel(offer)}
            </button>
            {busy && (
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={cancel}>
                Cancel
              </button>
            )}
          </span>
        </div>
      )}
      {result && (
        <div data-testid="line-stronger-result">
          <div className="review-ai-compare">
            <div>
              <span className="muted">Current</span>
              <div className="review-ai-text">{line.en || <span className="muted">Not translated</span>}</div>
            </div>
            <div>
              <span className="muted">With {offer.engineLabel}</span>
              <div className="review-ai-text" data-testid="line-stronger-text">{result.text}</div>
            </div>
          </div>
          <p className="muted" data-testid="line-stronger-cost">
            Cost: {formatUsd(result.cost_usd)}
            {same && ` · ${offer.engineLabel} gave the same English.`}
          </p>
          {stale && <p className="error" role="alert">{STALE_MESSAGE}</p>}
          <div className="review-actions">
            {!same && (
              <button
                type="button"
                className={buttonClass('secondary', 'sm')}
                disabled={applying || stale}
                onClick={() => use(result.text)}
              >
                Use this
              </button>
            )}
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={applying} onClick={() => setResult(null)}>
              Dismiss
            </button>
          </div>
        </div>
      )}
      {errorText !== null ? (
        <div className="banner error-banner" role="alert" data-testid="line-stronger-error">
          <span>
            {errorText}
            {error instanceof ApiError && error.status === 503 && <> <a href="#/settings">Open Settings</a></>}
          </span>
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      ) : (
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      )}
    </div>
  )
}
