// The series page's cross-episode consistency report: read-only until a flag's redo is confirmed.
import { useState } from 'react'

import { getConsistencyReport, type ConsistencyRendering, type SeriesConsistencyReport, type SeriesPageData } from '../../api/series'
import { redoGlossaryFlagged } from '../../api/translateStage'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { dramaName } from '../../components/libraryView'
import { buttonClass } from '../../components/uiClasses'
import { plural } from '../../labels'
import { consistencySummaryText } from './seriesModel'


function FlagRedo({ rendering, onDone, onError }: {
  rendering: ConsistencyRendering
  onDone: (message: string) => void
  onError: (e: unknown) => void
}) {
  const [busy, setBusy] = useState(false)
  const shown = rendering.titles.reduce((n, t) => n + t.line_ids.length, 0)
  const run = async () => {
    setBusy(true)
    onError(null)
    let started = 0
    try {
      // One run per title: the redo works on a single title's lines.
      for (const t of rendering.titles) {
        if (!t.line_ids.length) continue
        await redoGlossaryFlagged(t.drama_id, t.line_ids)
        started += 1
      }
      onDone(`Started a redo in ${plural(started, 'title', 'titles')}. Check Jobs; the report refreshes when you check again.`)
    } catch (e) {
      onError(e)
      if (started) onDone(`Started a redo in ${plural(started, 'title', 'titles')} before the error.`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <ConfirmButton
      name={`${plural(shown, 'line', 'lines')} as "${rendering.text}"`}
      label={`Redo ${plural(shown, 'line', 'lines')}…`}
      confirmLabel="Confirm redo with the stronger engine"
      verb="redo"
      tone="primary"
      size="sm"
      busy={busy}
      onConfirm={run}
    />
  )
}

export function ConsistencyCard({ data }: { data: SeriesPageData }) {
  const [report, setReport] = useState<SeriesConsistencyReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const names = new Map(data.titles.map((t) => [t.id, dramaName(t)]))

  const check = () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    getConsistencyReport(data.id).then(
      (r) => { setBusy(false); setReport(r) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }

  return (
    <Card title="Name consistency across episodes">
      <p className="muted">
        Lists every English rendering of each glossary term (and its aliases) found in the series' translated lines, and flags
        the ones that are not the glossary's English. Checking changes nothing; a redo runs only when you confirm it.
      </p>
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={check} data-testid="series-consistency-check">
          {busy ? 'Checking…' : report ? 'Check again' : 'Check consistency'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {notice && <p className="status-line" role="status" data-testid="series-consistency-notice">{notice}</p>}
      {report && (
        <div data-testid="series-consistency-report">
          <p role="status">{consistencySummaryText(report.summary)}</p>
          {report.truncated && <p className="muted">Only part of the series was checked (size limit).</p>}
          {!report.terms.length && <p className="muted">No glossary term with an English translation appears in the translated lines.</p>}
          <ul className="series-consistency-terms">
            {report.terms.map((t) => (
              <li key={t.term_id}>
                <strong>{t.term_original}</strong> <span className="muted">→ {t.term_translation}</span>
                <ul className="series-consistency-renderings">
                  {t.renderings.map((r) => (
                    <li key={`${r.kind}:${r.text}`} className={r.flagged ? 'warn' : undefined}>
                      <span>
                        {r.kind === 'unnamed' ? 'No name in the line' : r.text} · {plural(r.count, 'line', 'lines')}
                        {r.flagged && ' · off-glossary'}
                      </span>
                      <span className="muted">
                        {' '}({r.titles.map((x) => `${names.get(x.drama_id) ?? `#${x.drama_id}`}: ${x.line_count}`).join(', ')})
                      </span>
                      {r.examples.length > 0 && (
                        <details>
                          <summary>Examples</summary>
                          <ul>
                            {r.examples.map((x) => (
                              <li key={x.line_id}>{names.get(x.drama_id) ?? `#${x.drama_id}`} · line {x.idx + 1}: {x.en}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                      {r.flagged && (
                        <FlagRedo rendering={r} onDone={(m) => setNotice(m)} onError={setError} />
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}
