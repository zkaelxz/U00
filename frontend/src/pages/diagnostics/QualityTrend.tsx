import { useEffect, useState } from 'react'

import { getQualityHistory } from '../../api/diagnostics'
import { ChartReadout } from '../../components/ChartReadout'
import { useChartReadout } from '../../hooks/useChartReadout'
import { ErrorBanner } from '../../components/ErrorBanner'
import type { QualityHistory, QualitySeries } from '../../types/diagnostics'
import { groupByLanguage, higherIsBetter, metricLabel, trendDirection, trendText } from './qualityFormat'

const W = 220
const H = 56
const PAD = 6

/** One clip's scores over the release checks: a line, a dot per run, and its baseline dashed. */
function Spark({ s }: { s: QualitySeries }) {
  const readout = useChartReadout()
  const scored = s.points.filter((p) => p.score != null)
  const values = [...scored.map((p) => p.score as number), ...s.points.map((p) => p.baseline).filter((b): b is number => b != null)]
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const x = (i: number) => (s.points.length < 2 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (s.points.length - 1))
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span
  const line = s.points.map((p, i) => (p.score == null ? null : `${x(i)},${y(p.score)}`)).filter(Boolean).join(' ')
  const baseline = s.points[s.points.length - 1]?.baseline
  return (
    <>
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`${s.id}: ${trendText(s)}`} className="quality-spark">
      {baseline != null && <line x1={PAD} x2={W - PAD} y1={y(baseline)} y2={y(baseline)} stroke="currentColor" strokeOpacity="0.4" strokeDasharray="4 3" />}
      {scored.length > 1 && <polyline points={line} fill="none" stroke="var(--accent, currentColor)" strokeWidth="2" />}
      {s.points.map((p, i) => p.score == null ? null : <circle key={p.when} cx={x(i)} cy={y(p.score)} r="3" fill="var(--accent, currentColor)" {...readout.point(`${s.id}, ${p.when}: ${p.score}`)} />)}
    </svg>
    <ChartReadout text={readout.text} hint="" />
    </>
  )
}

/** "Over releases": every release check on this PC as a trend per clip, grouped by language. Fed by the
 *  result files each release check already writes, so it needs nothing kept up by hand. */
export function QualityTrend() {
  const [data, setData] = useState<QualityHistory | null>(null)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    let live = true
    getQualityHistory().then((h) => { if (live) setData(h) }, (e) => { if (live) setError(e) })
    return () => { live = false }
  }, [])
  if (error) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  if (!data || data.series.length === 0) return null
  return (
    <div data-testid="quality-trend">
      <h4>Over releases</h4>
      <p className="muted">{data.runs === 1 ? 'One release check so far; a trend needs two.' : `${data.runs} release checks. A dashed line is the baseline.`}</p>
      {groupByLanguage(data.series).map((g) => (
        <section key={g.language} aria-label={g.name}>
          <h5>{g.name}</h5>
          <ul className="quality-trend-list">
            {g.series.map((s) => {
              const dir = trendDirection(s)
              return (
                <li key={s.id}>
                  <strong>{s.id}</strong> <span className="muted">{metricLabel({ kind: s.kind, metric: s.metric } as never)}{higherIsBetter(s) ? '' : ', lower is better'}</span>
                  <Spark s={s} />
                  {dir && <span className="muted">Latest run: {dir === 'same' ? 'same as the run before' : `${dir} than the run before`}</span>}
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </div>
  )
}
