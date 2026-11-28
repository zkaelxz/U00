import { useState } from 'react'

import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { formatCost } from './benchmarkForm'
import { bestValueRow, costRanks, HAND_RUN_TESTS, qualityRanks, speedRanks, type HandRunRow, type HandRunTest } from './handRunResults'
import { competitionRanks, sortedOrder, type SortKey } from './runRanking'
import { SortBar } from './SortBar'

const PER_2000_LINES = 2000 / 90
const NOT_MEASURED = <span className="muted">not measured</span>
const JUDGED = 'judged by reading, not scored'

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
  const quality = qualityRanks(test)
  const value = bestValueRow(test)
  const hasTime = test.rows.some((r) => r.seconds !== undefined)
  const hasCost = test.rows.some((r) => r.costUsd !== undefined)
  const [sort, setSort] = useState<SortKey>('quality')
  const key: SortKey = (sort === 'speed' && !hasTime) || (sort === 'cost' && !hasCost) ? 'quality' : sort
  const byKey = { quality: quality.ranks, speed, cost }[key]
  // Ties keep the written order, so the same tier always reads in the same sequence.
  const order = sortedOrder(key === 'quality' ? byKey : competitionRanks(test.rows.map((r) => (key === 'speed' ? r.seconds : r.costUsd)), false))
  return (
    <Section
      title={test.title}
      summary={`${test.date}${test.outdated ? ' · older' : ''} · ${test.rows.length} models`}
      storageKey={`benchmark.hand.${test.id}`}
      defaultOpen={!test.outdated}
      keepFold
    >
      <div className="bench-hand-test">
        {test.outdated && <p className="muted"><Badge tone="warn">Older</Badge> {test.outdated}</p>}
        <p className="muted">{test.date}. {test.note} Source: {test.source}.</p>
        {quality.judged && <p className="muted">Quality rank: {JUDGED} (1 = best; equal readings share a rank).</p>}
        <SortBar name={test.id} value={key} onChange={setSort} hide={[...(hasTime ? [] : ['speed' as const]), ...(hasCost ? [] : ['cost' as const])]} />
        <div className="table-scroll">
          <table className="bench-table" aria-label={`Hand-run results: ${test.title}`}>
            <thead>
              <tr>
                <th>Model and setting</th>
                <th className="num">Quality rank</th>
                {test.scoreName && <th className="num">{test.scoreName}</th>}
                {hasTime && <th className="num">Time ({test.basis})</th>}
                {hasTime && <th className="num">Speed rank</th>}
                <th className="num">Cost ({test.basis})</th>
                {test.rankCost && <th className="num">Cost rank</th>}
                <th>Quality reading</th>
              </tr>
            </thead>
            <tbody>
              {order.map((i) => {
                const r = test.rows[i]
                return (
                  <tr key={r.model} className={r.outdated ? 'bench-outdated' : undefined}>
                    <td>
                      {r.model}
                      {r.outdated && <div><Badge tone="warn" title={r.outdated}>Outdated</Badge> <span className="muted">{r.outdated}</span></div>}
                    </td>
                    <td className="num" title={quality.judged && quality.ranks[i] != null ? JUDGED : undefined}>
                      {quality.ranks[i] ?? <span className="muted">—</span>}
                      {quality.judged && quality.ranks[i] != null && <div className="muted">by reading</div>}
                    </td>
                    {test.scoreName && <td className="num">{r.score !== undefined ? r.score.toFixed(1) : <span className="muted">—</span>}</td>}
                    {hasTime && <td className="num">{timeCell(r)}</td>}
                    {hasTime && <td className="num">{speed[i] ?? <span className="muted">—</span>}</td>}
                    <td className="num">
                      {costCell(r, !!test.scaleTo2000Lines)}
                      {i === value && <div><Badge tone="ok" title="Most quality per dollar among the top-quality rows">Best value</Badge></div>}
                    </td>
                    {test.rankCost && <td className="num">{cost[i] ?? <span className="muted">—</span>}</td>}
                    <td>{r.reading}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </Section>
  )
}

/** Model comparisons run by hand, newest first, shown apart from the runs the app recorded. */
export function HandRunResultsCard() {
  return (
    <Section title="Model comparisons run by hand" summary="Newest first; not recorded by the app" storageKey="benchmark.handRuns" keepFold>
      <p className="muted">
        One run each, no native-speaker check, so differences of a line or two are within run-to-run noise. Where a test has a score
        (chrF) the quality rank follows it; otherwise it follows the written reading and is marked as judged by reading. Best value is the
        most quality per dollar among the top-quality rows. Each test shows its date and source; older tests are marked.
      </p>
      {HAND_RUN_TESTS.map((t) => <HandRunTable key={t.id} test={t} />)}
    </Section>
  )
}
