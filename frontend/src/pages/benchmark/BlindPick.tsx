import { useCallback, useEffect, useState } from 'react'

import { getBenchmarkPair, getBenchmarkPickSummary, saveBenchmarkPick } from '../../api/benchmark'
import type { BenchmarkPairNext, BenchmarkPickChoice, BenchmarkPickSummary } from '../../types/benchmark'
import { buttonClass } from '../../components/uiClasses'
import { plainError } from './benchmarkForm'
import { pickSummaryLine } from './blindPickText'

/** Pick the better of two runs' outputs without being told which run made which. */
export function BlindPick({ runA, runB, onSaved }: { runA: number; runB: number; onSaved: () => void }) {
  const [pair, setPair] = useState<BenchmarkPairNext | null>(null)
  const [summary, setSummary] = useState<BenchmarkPickSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [next, sums] = await Promise.all([getBenchmarkPair(runA, runB), getBenchmarkPickSummary(runA, runB)])
      setPair(next)
      setSummary(sums)
      setError(null)
    } catch (e) {
      setError(plainError(e))
    }
  }, [runA, runB])

  useEffect(() => {
    void load()
  }, [load])

  const choose = async (choice: BenchmarkPickChoice) => {
    if (!pair?.ticket) return
    setBusy(true)
    try {
      await saveBenchmarkPick(pair.ticket, choice)
      await load()
      onSaved()
    } catch (e) {
      setError(plainError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="bench-blind" aria-label="Pick the better output" data-testid="blind-pick">
      <h3>Which reads better?</h3>
      <p className="muted">You are not told which run made which. Your picks are kept beside the scores.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {pair && pair.done && <p className="muted">{pair.total === 0 ? 'These runs have no differing outputs to compare.' : 'You have picked every case.'}</p>}
      {pair && !pair.done && (
        <>
          <p className="muted num">{pair.label} · {pair.remaining} left of {pair.total}</p>
          {pair.source_text && <p className="bench-blind-source">{pair.source_text}</p>}
          <div className="bench-blind-sides">
            <p className="bench-output" data-testid="blind-left">{pair.left_text}</p>
            <p className="bench-output" data-testid="blind-right">{pair.right_text}</p>
          </div>
          <div className="pill-row">
            <button type="button" className={buttonClass('primary', 'sm')} disabled={busy} onClick={() => choose('left')}>Left is better</button>
            <button type="button" className={buttonClass('primary', 'sm')} disabled={busy} onClick={() => choose('right')}>Right is better</button>
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => choose('tie')}>About the same</button>
          </div>
        </>
      )}
      {summary && summary.picked > 0 && <p className="muted num" data-testid="blind-summary">{pickSummaryLine(summary)}</p>}
    </section>
  )
}
