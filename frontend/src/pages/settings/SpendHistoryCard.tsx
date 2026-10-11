/*
 * Settings > Spend history: paid-API spend by month, and what caused a month.
 * Read-only. Figures are the stored estimates the monthly cap also counts, so
 * re-costing past costs changes them. PC only, like the rest of the spend settings.
 */
import { useEffect, useState } from 'react'

import { getSpendHistory, monthLabel, money, SPEND_HISTORY_CSV, type SpendBreakdownRow, type SpendHistory } from '../../api/spendHistory'
import { Card } from '../../components/Card'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'

const TITLE = 'Spend history'

export function SpendHistoryCard() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <History />
}

function History() {
  const [data, setData] = useState<SpendHistory | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(true)

  const load = (month?: string) => {
    setBusy(true)
    setError(null)
    getSpendHistory(month).then(
      (d) => {
        setData(d)
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }
  useEffect(() => load(), [])

  const selected = data?.selected_month ?? null
  return (
    <Card title={TITLE} meta={data?.months.length ? `last ${data.max_months} months` : undefined} aria-label={TITLE}>
      <p className="muted">
        Estimates, priced from the app&rsquo;s price table. Same figures as the monthly cap, so applying past costs
        changes them.
      </p>
      {error ? <ErrorBanner error={error} /> : null}
      {data && data.months.length === 0 ? <p data-testid="spend-empty">No paid calls logged yet.</p> : null}
      {data && data.months.length > 0 ? (
        <>
          <table className="data-table" aria-label="Spend by month">
            <thead>
              <tr>
                <th>Month</th>
                <th>Spend</th>
                <th>Calls</th>
              </tr>
            </thead>
            <tbody>
              {data.months.map((m) => (
                <tr key={m.month}>
                  <td>
                    <button
                      type="button"
                      className={buttonClass('ghost')}
                      aria-pressed={m.month === selected}
                      disabled={busy}
                      onClick={() => load(m.month)}
                    >
                      {monthLabel(m.month)}
                    </button>
                  </td>
                  <td>
                    {money(m.cost_usd)}
                    {m.overlaps_reset && m.cap_counted_usd !== null ? (
                      <div className="muted">Cap counts {money(m.cap_counted_usd)} since reset</div>
                    ) : null}
                  </td>
                  <td>{m.calls}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected ? (
            <div data-testid="spend-breakdown">
              <h3>{monthLabel(selected)}</h3>
              <Breakdown title="By operation" rows={data.by_operation} />
              <Breakdown title="By engine and model" rows={data.by_engine_model} />
              <Breakdown title="By title" rows={data.by_title} />
            </div>
          ) : null}
          <div className="actions">
            <ButtonLink variant="secondary" href={SPEND_HISTORY_CSV} download>
              Download CSV
            </ButtonLink>
          </div>
        </>
      ) : null}
    </Card>
  )
}

function Breakdown({ title, rows }: { title: string; rows: SpendBreakdownRow[] }) {
  return (
    <table className="data-table" aria-label={title}>
      <thead>
        <tr>
          <th>{title}</th>
          <th>Spend</th>
          <th>Calls</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.is_other ? 'other' : r.label}`}>
            <td>{r.label}</td>
            <td>{money(r.cost_usd)}</td>
            <td>{r.calls}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
