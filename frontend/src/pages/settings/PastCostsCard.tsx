/*
 * Settings > Past costs: an opt-in fix for old spend figures that were priced
 * at a higher model's rate. "Check past costs" previews (writes nothing);
 * Apply rewrites those entries only after a confirm; Undo puts the old
 * figures back. PC only: away from the PC the Card shows the usual note.
 */
import { useEffect, useState } from 'react'

import { applyUsageRecost, getUsageRecost, recostSummary, undoUsageRecost, usd, type UsageRecostPreview } from '../../api/usageRecost'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'

const TITLE = 'Past costs'

export function PastCostsCard() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <Controls />
}

function Controls() {
  const [preview, setPreview] = useState<UsageRecostPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [recosted, setRecosted] = useState(0)

  // Whether Undo is offered is known before anything is checked.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getUsageRecost().then((p) => live && setRecosted(p.recosted_rows), () => undefined)
    })
    return () => {
      live = false
    }
  }, [])

  const run = (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    work().then(
      () => setBusy(false),
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const check = () =>
    run(async () => {
      const p = await getUsageRecost()
      setPreview(p)
      setRecosted(p.recosted_rows)
      setResult(null)
    })

  const apply = () =>
    run(async () => {
      const r = await applyUsageRecost(preview?.rows ?? 0)
      setPreview(null)
      setRecosted((n) => n + r.rows)
      setResult(`Re-costed ${r.rows} ${r.rows === 1 ? 'entry' : 'entries'}. This month now shows ${usd(r.month_spend_usd)}.`)
    })

  const undo = () =>
    run(async () => {
      const r = await undoUsageRecost()
      setPreview(null)
      setRecosted(0)
      setResult(`Restored ${r.rows} ${r.rows === 1 ? 'entry' : 'entries'}. This month now shows ${usd(r.month_spend_usd)}.`)
    })

  return (
    <Card title={TITLE} meta={recosted > 0 ? `${recosted} re-costed` : undefined} aria-label={TITLE}>
      <p className="muted">
        Some past usage was priced at a higher model&rsquo;s rate, so spend totals can read too high. Check what would
        change; nothing is written until you apply it, and Undo restores the old figures.
      </p>
      {error ? <ErrorBanner error={error} /> : null}
      {preview ? (
        <>
          <p data-testid="recost-summary">{recostSummary(preview)}</p>
          {preview.rows > 0 ? (
            <table className="data-table" aria-label="Entries that would change">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Entries</th>
                  <th>Logged now</th>
                  <th>About, re-costed</th>
                </tr>
              </thead>
              <tbody>
                {preview.models.map((m) => (
                  <tr key={m.model}>
                    <td>{m.model}</td>
                    <td>{m.rows}</td>
                    <td>{usd(m.stored_usd)}</td>
                    <td>{usd(m.recomputed_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </>
      ) : null}
      {result ? <p role="status" data-testid="recost-result">{result}</p> : null}
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={check}>
          Check past costs
        </button>
        {preview && preview.rows > 0 ? (
          <ConfirmButton name="past costs" label="Apply…" verb="apply" tone="primary" busy={busy} onConfirm={apply} />
        ) : null}
        {recosted > 0 ? (
          <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={undo}>
            Undo re-cost
          </button>
        ) : null}
      </div>
    </Card>
  )
}
