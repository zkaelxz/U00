import { formatCost } from './benchmarkForm'
import { costRanks, HAND_RUN_TESTS, speedRanks, type HandRunRow, type HandRunTest } from './handRunResults'

const PER_2000_LINES = 2000 / 90
const NOT_MEASURED = <span className="muted">not measured</span>

function timeCell(r: HandRunRow) {
  if (r.timeLabel) return r.timeLabel
  return r.seconds === undefined ? NOT_MEASURED : `${r.seconds.toFixed(1)} s`
}

function costCell(r: HandRunRow, scale: boolean) {
  if (r.costLabel) return r.costLabel
  if (r.costUsd === undefined) return NOT_MEASURED
  if (r.costUsd === 0) return 'free'
  return (
    <>
      {formatCost(r.costUsd)}{r.costEstimated ? ' (estimate)' : ''}
      {scale && <div className="muted">~{formatCost(r.costUsd * PER_2000_LINES)} per 2,000 lines</div>}
    </>
  )
}

function HandRunTable({ test }: { test: HandRunTest }) {
  const speed = speedRanks(test.rows)
  const cost = costRanks(test.rows)
  const showSpeedRank = test.rows.some((r) => r.seconds !== undefined)
  return (
    <section>
      <h3>{test.title}</h3>
      <p className="muted">{test.note} Source: {test.source}.</p>
      <div className="table-scroll">
        <table className="bench-table" aria-label={`Hand-run results: ${test.title}`}>
          <thead>
            <tr>
              <th>Model and setting</th>
              <th className="num">Time ({test.basis})</th>
              {showSpeedRank && <th className="num">Speed rank</th>}
              <th className="num">Cost ({test.basis})</th>
              {test.rankCost && <th className="num">Cost rank</th>}
              <th>Quality reading</th>
            </tr>
          </thead>
          <tbody>
            {test.rows.map((r, i) => (
              <tr key={r.model}>
                <td>{r.model}</td>
                <td className="num">{timeCell(r)}</td>
                {showSpeedRank && <td className="num">{speed[i] ?? <span className="muted">—</span>}</td>}
                <td className="num">{costCell(r, !!test.scaleTo2000Lines)}</td>
                {test.rankCost && <td className="num">{cost[i] ?? <span className="muted">—</span>}</td>}
                <td>{r.reading}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Reference results from tests run by hand, shown apart from the runs the app recorded. */
export function HandRunResultsCard() {
  return (
    <details className="bench-hand-run">
      <summary>Earlier hand-run results (not recorded by the app)</summary>
      <p className="muted">
        One run each, judged by reading, no native-speaker check. Quality is a reading, not a score, so only speed (or cost, where speed was not measured) is ranked. Differences of a line or two are within run-to-run noise.
      </p>
      {HAND_RUN_TESTS.map((t) => <HandRunTable key={t.id} test={t} />)}
    </details>
  )
}
