import { formatCost } from './benchmarkForm'
import { HAND_RUN_ROWS, HAND_RUN_TEST, speedRanks } from './handRunResults'

const PER_2000_LINES = 2000 / 90

/** Reference results from tests run by hand, shown apart from the runs the app recorded. */
export function HandRunResultsCard() {
  const ranks = speedRanks(HAND_RUN_ROWS)
  return (
    <details className="bench-hand-run">
      <summary>Earlier hand-run results (not recorded by the app)</summary>
      <p className="muted">
        {HAND_RUN_TEST.title}. {HAND_RUN_TEST.note} Full write-ups: {HAND_RUN_TEST.source}. Quality is a reading, not a score, so only speed is ranked.
      </p>
      <div className="table-scroll">
        <table className="bench-table" aria-label="Hand-run results">
          <thead>
            <tr>
              <th>Model and setting</th>
              <th className="num">Time (90 lines)</th>
              <th className="num">Speed rank</th>
              <th className="num">Cost (90 lines)</th>
              <th className="num">Cost (2,000 lines)</th>
              <th>Quality reading</th>
            </tr>
          </thead>
          <tbody>
            {HAND_RUN_ROWS.map((r, i) => (
              <tr key={r.model}>
                <td>{r.model}</td>
                <td className="num">{r.seconds.toFixed(1)} s</td>
                <td className="num">{ranks[i]}</td>
                <td className="num">{r.costUsd === 0 ? 'free' : formatCost(r.costUsd)}{r.costEstimated ? ' (estimate)' : ''}</td>
                <td className="num">{r.costUsd === 0 ? 'free' : `~${formatCost(r.costUsd * PER_2000_LINES)}`}</td>
                <td>{r.reading}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        Gemini 3.1 Flash-Lite is left out: it returned nothing for the second batch of 30 lines in every run, so it can't be rated yet.
      </p>
    </details>
  )
}
