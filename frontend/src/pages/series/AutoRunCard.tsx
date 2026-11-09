// The series page's auto-run switch: new episodes go through Transcribe, Translate, Auto QC and Export on the queue.
import { useCallback, useEffect, useState } from 'react'

import {
  continueSeriesAutoRun, getSeriesAutoRun, setSeriesAutoRunCeiling, turnOffSeriesAutoRun, turnOnSeriesAutoRun,
  type SeriesAutoRun, type SeriesPageData,
} from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useLoad } from '../../hooks/useLoad'
import { DEFAULT_CEILING, ceilingProblem, money } from './seriesModel'

const REFRESH_MS = 15000

function Body({ data, run, onChange }: { data: SeriesPageData; run: SeriesAutoRun; onChange: (r: SeriesAutoRun) => void }) {
  const [ceiling, setCeiling] = useState(String(run.on ? run.ceiling_usd : DEFAULT_CEILING))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const act = (call: () => Promise<SeriesAutoRun>) => {
    setBusy(true)
    setError(null)
    call().then(
      (r) => { setBusy(false); onChange(r) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const withCeiling = (call: (usd: number) => Promise<SeriesAutoRun>) => {
    const bad = ceilingProblem(ceiling)
    setProblem(bad)
    if (!bad) act(() => call(Number(ceiling)))
  }
  const editable = data.can_edit

  return (
    <>
      <p className="muted">
        When this is on, each episode in the series that has audio or video and no lines yet goes through Transcribe,
        Translate, Auto QC and Export on the Jobs queue, one episode at a time, with the title's saved settings. It
        stops after the first episode so you can check the subtitles, then goes on when you press Continue. It also
        stops when an episode fails and when the spending ceiling is reached. The ceiling covers all episodes it runs
        together (not the monthly cap), and the translation run for each episode is capped to what is left of it.
        Start the queue on the Jobs page for anything to run.
      </p>
      {run.on && (
        <p data-testid="series-auto-status" role="status">
          On. Spent {money(run.spent_usd)} of {money(run.ceiling_usd)} on {run.episodes_run} episode{run.episodes_run === 1 ? '' : 's'}.
        </p>
      )}
      {run.on && run.hold && (
        <div className="status-line" role="status" data-testid="series-auto-hold">
          <p>{run.hold}</p>
          {editable && run.hold_kind !== 'ceiling' && (
            <button type="button" className={buttonClass('primary')} disabled={busy}
              onClick={() => act(() => continueSeriesAutoRun(data.id))}>Continue</button>
          )}
        </div>
      )}
      <Field label="Spending ceiling (USD)" help={problem ?? undefined}>
        <input type="number" inputMode="decimal" min="0.01" step="0.01" value={ceiling} disabled={busy || !editable}
          aria-invalid={problem ? true : undefined} data-testid="series-auto-ceiling"
          onChange={(e) => setCeiling(e.target.value)} />
      </Field>
      {editable && (
        <div className="actions">
          {!run.on && (
            <button type="button" className={buttonClass('primary')} disabled={busy}
              onClick={() => withCeiling((usd) => turnOnSeriesAutoRun(data.id, usd))}>Turn on</button>
          )}
          {run.on && (
            <>
              <button type="button" className={buttonClass('secondary')} disabled={busy}
                onClick={() => withCeiling((usd) => setSeriesAutoRunCeiling(data.id, usd))}>Save ceiling</button>
              <button type="button" className={buttonClass('ghost')} disabled={busy}
                onClick={() => act(() => turnOffSeriesAutoRun(data.id))}>Turn off</button>
            </>
          )}
        </div>
      )}
      {!editable && <p className="muted">You can view this but not change it.</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}

export function AutoRunCard({ data }: { data: SeriesPageData }) {
  const [reloadKey, setReloadKey] = useState(0)
  const load = useCallback(() => getSeriesAutoRun(data.id), [data.id])
  const state = useLoad(load, reloadKey)
  const [latest, setLatest] = useState<SeriesAutoRun | null>(null)
  const run = latest ?? state.data
  useEffect(() => {
    // The queue runs on the server, so a stop for review appears without a click here.
    const t = window.setInterval(() => { setLatest(null); setReloadKey((k) => k + 1) }, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [])
  return (
    <Card title="Run new episodes automatically">
      <ErrorBanner error={state.error} />
      {!run && !state.error && <p className="muted">Loading…</p>}
      {/* Keyed so the ceiling box restarts from the stored value after a switch on or off. */}
      {run && <Body key={`${run.on}-${run.ceiling_usd}`} data={data} run={run} onChange={setLatest} />}
    </Card>
  )
}
