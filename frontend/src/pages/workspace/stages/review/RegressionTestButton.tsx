import { useEffect, useRef, useState } from 'react'

import { addRegressionCase } from '../../../../api/benchmark'
import { describeError } from '../../../../components/errorMessages'
import { usePcOnly } from '../../../../hooks/usePcOnly'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine } from '../../../../types/review'

const CONFIRM_TIMEOUT_MS = 5000

// "Add as regression test" in the line sheet. Keeps this line's
// source and its current English as a Benchmark Lab regression case, so the
// next benchmark run checks it. Never automatic: two presses, PC only (the
// route is local_only), hidden elsewhere.
export function RegressionTestButton({ dramaId, line }: { dramaId: number; line: ReviewLine }) {
  const pc = usePcOnly()
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!armed) return
    confirmRef.current?.focus()
    const t = setTimeout(() => setArmed(false), CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [armed])
  if (pc !== 'local') return null
  const missing = !line.zh || !line.en
  const add = () => {
    setBusy(true)
    setNote(null)
    addRegressionCase(dramaId, line.id).then(
      (r) => setNote(r.replaced ? 'Regression test updated with this translation.' : 'Added to the Benchmark Lab regression tests.'),
      (e: unknown) => setNote(describeError(e, { pcOnly: true }).title),
    ).finally(() => {
      setBusy(false)
      setArmed(false)
    })
  }
  return (
    <>
      {armed ? (
        <button ref={confirmRef} type="button" disabled={busy} onClick={add} data-testid="regression-confirm">
          {busy ? 'Adding…' : `Confirm: keep #${lineNumber(line.idx)} as a regression test`}
        </button>
      ) : (
        <button type="button" disabled={missing} onClick={() => setArmed(true)}>
          Add as regression test…
          {missing
            ? <span className="sheet-reason">Needs source text and a translation.</span>
            : <span className="sheet-reason">The next Benchmark Lab run checks this line.</span>}
        </button>
      )}
      {note && <p className="muted" role="status">{note}</p>}
    </>
  )
}
