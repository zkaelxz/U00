import { useEffect, useRef, useState } from 'react'

import {
  getBenchmarkArena, getBenchmarkRun, type BenchmarkArena, type BenchmarkResult, type BenchmarkRun,
} from '../../api/benchmark'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { buttonClass } from '../../components/uiClasses'
import {
  arenaRunNames, deltaTone, formatCost, formatDelta, formatLatency, formatScore, metricName, metricNote, mixedScorerNote, plainError,
  runConfigLabel, runStatusLabel, runStatusTone, tierLabel, tierTone,
} from './benchmarkForm'

/** What to show: 2-4 runs side by side, or one run's own results. */
export type ArenaTarget = { kind: 'arena'; runIds: number[] } | { kind: 'run'; runId: number }

/** A single run's results in the Arena's shape (no source/reference: GET /runs/{id} doesn't carry them). */
function singleRunArena(run: BenchmarkRun, results: BenchmarkResult[]): BenchmarkArena {
  return {
    runs: [run],
    rows: results.map((r) => ({ case_id: r.case_id, label: r.case_label, source_text: null, reference_text: null, tier: null, results: [r] })),
  }
}

export function ArenaView({ target, phone, onClose }: { target: ArenaTarget; phone: boolean; onClose: () => void }) {
  const [data, setData] = useState<{ key: string; arena: BenchmarkArena } | null>(null)
  const [failure, setFailure] = useState<{ key: string; text: string } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const key = target.kind === 'arena' ? `a:${target.runIds.join(',')}` : `r:${target.runId}`

  useEffect(() => {
    let live = true
    const load = target.kind === 'arena'
      ? getBenchmarkArena(target.runIds)
      : getBenchmarkRun(target.runId).then((d) => singleRunArena(d.run, d.results))
    load.then(
      (arena) => live && setData({ key, arena }),
      (e: unknown) => live && setFailure({ key, text: plainError(e) }),
    )
    return () => {
      live = false
    }
  }, [target, key])

  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: 'start' })
  }, [key])

  const arena = data?.key === key ? data.arena : null
  const error = failure?.key === key ? failure.text : null
  const many = target.kind === 'arena'
  const title = many ? 'Model Arena' : 'Run results'
  const stage = arena?.runs[0]?.stage ?? null
  const names = arena ? arenaRunNames(arena.runs) : []

  return (
    <div ref={ref} className="bench-arena-anchor">
      <Card
        title={title}
        meta={arena ? `${arena.rows.length} ${arena.rows.length === 1 ? 'case' : 'cases'}${many ? ` · ${arena.runs.length} runs` : ''}` : undefined}
        actions={
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>
            Close
          </button>
        }
        className="bench-arena"
        aria-label={title}
      >
        {!arena && !error && <p className="muted">Loading…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {arena && (
          <>
            <p className="muted">{metricNote(stage)}</p>
            {mixedScorerNote(arena.rows) && (
              <p className="warn" role="note" data-testid="bench-mixed-scorers">
                {mixedScorerNote(arena.rows)}
              </p>
            )}
            <ol className="bench-arena-runs" data-cols={phone ? 1 : arena.runs.length} aria-label="Runs compared">
              {arena.runs.map((r, i) => (
                <li key={r.id} className="bench-arena-run">
                  <strong>{runConfigLabel(r)}</strong>
                  <span className="muted">
                    {r.label || `Run ${r.id}`}
                    {r.prompt_version ? ` · prompt ${r.prompt_version}` : ''}
                  </span>
                  <span className="bench-score num">{formatScore(r.aggregate_score)}</span>
                  <span className="pill-row">
                    {many && i > 0 && <Badge tone={deltaTone(r.delta_vs_first)}>{formatDelta(r.delta_vs_first)} vs first</Badge>}
                    {many && i === 0 && <Badge tone="neutral">Baseline</Badge>}
                    <Badge tone={runStatusTone(r.status)}>{runStatusLabel(r.status)}</Badge>
                  </span>
                  <span className="muted num">
                    {r.passed_count}/{r.scored_count} passed · {formatLatency(r.avg_latency_seconds)} avg · {formatCost(r.total_cost_usd)}
                    {r.error_count ? ` · ${r.error_count} errors` : ''}
                  </span>
                  {r.note && <span className="muted">{r.note}</span>}
                </li>
              ))}
            </ol>
            {arena.rows.length === 0 ? (
              <p className="muted">No results recorded yet.</p>
            ) : (
              <ol className="bench-arena-rows" aria-label="Cases">
                {arena.rows.map((row, idx) => (
                  <li key={row.case_id ?? `i${idx}`} className="bench-arena-row">
                    <div className="bench-arena-case">
                      <strong>{row.label || `Case ${row.case_id ?? idx + 1}`}</strong>
                      {row.tier && <Badge tone={tierTone(row.tier)}>{tierLabel(row.tier)}</Badge>}
                    </div>
                    {(row.source_text || row.reference_text) && (
                      <dl className="bench-arena-texts">
                        {row.source_text && (
                          <div>
                            <dt>Source</dt>
                            <dd>{row.source_text}</dd>
                          </div>
                        )}
                        <div>
                          <dt>Reference</dt>
                          <dd>{row.reference_text || <span className="muted">None (not scored)</span>}</dd>
                        </div>
                      </dl>
                    )}
                    <div className="bench-arena-cells" data-cols={phone ? 1 : arena.runs.length}>
                      {row.results.map((res, i) => (
                        <ResultCell key={i} res={res} name={names[i] ?? ''} showRun={many} />
                      ))}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </Card>
    </div>
  )
}

function ResultCell({ res, name, showRun }: { res: BenchmarkResult | null; name: string; showRun: boolean }) {
  return (
    <div className="bench-arena-cell" aria-label={showRun ? `Output of ${name}` : 'Output'}>
      {showRun && <span className="bench-cell-run muted">{name}</span>}
      {!res ? (
        <p className="muted">Not run on this case.</p>
      ) : (
        <>
          {res.error ? <p className="error">{res.error}</p> : <p className="bench-output">{res.output_text || <span className="muted">(empty)</span>}</p>}
          <span className="pill-row">
            {res.passed === true && <Badge tone="ok">Pass</Badge>}
            {res.passed === false && <Badge tone="bad">Fail</Badge>}
            {res.passed == null && <Badge tone="neutral">Not scored</Badge>}
            {res.score != null && (
              <span className="muted num">
                {formatScore(res.score)} {metricName(res.metric)}
                {res.scorer === 'jiwer' ? ' (jiwer)' : ''}
              </span>
            )}
            {res.duration_seconds != null && <span className="muted num">{formatLatency(res.duration_seconds)}</span>}
          </span>
        </>
      )}
    </div>
  )
}
