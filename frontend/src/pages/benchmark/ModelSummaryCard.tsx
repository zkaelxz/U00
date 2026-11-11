import { stageLabel, formatCost, formatLatency, formatScore } from './benchmarkForm'
import { summariseModels } from './modelSummary'
import type { BenchmarkRun } from '../../types/benchmark'

const NOT_MEASURED = 'not measured'

const cell = (text: string) => (text === '—' ? <span className="muted">{NOT_MEASURED}</span> : text)

/** One row per model, averaged over the finished runs listed below it. */
export function ModelSummaryCard({ runs }: { runs: BenchmarkRun[] }) {
  const rows = summariseModels(runs)
  if (rows.length === 0) return null
  return (
    <div className="table-scroll">
      <table className="bench-table" aria-label="Model summary">
        <caption className="muted">Average per model over the finished runs shown below. Ranks are within each stage (1 = best).</caption>
        <thead>
          <tr>
            <th>Model</th>
            <th>Stage</th>
            <th className="num">Runs</th>
            <th className="num">Quality</th>
            <th className="num">Quality rank</th>
            <th className="num">Avg time</th>
            <th className="num">Speed rank</th>
            <th className="num">Avg cost / run</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.name}</td>
              <td>{stageLabel(r.stage)}</td>
              <td className="num">{r.runs}</td>
              <td className="num">{cell(formatScore(r.score))}</td>
              <td className="num">{r.qualityRank ?? <span className="muted">—</span>}</td>
              <td className="num">{cell(formatLatency(r.latency))}</td>
              <td className="num">{r.speedRank ?? <span className="muted">—</span>}</td>
              <td className="num">{cell(formatCost(r.cost))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
