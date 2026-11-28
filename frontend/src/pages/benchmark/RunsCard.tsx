import { useState, type ReactNode } from 'react'

import { deleteBenchmarkRun } from '../../api/benchmark'
import type { BenchmarkRun } from '../../types/benchmark'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { buttonClass } from '../../components/uiClasses'
import { usePersistedState } from '../../hooks/usePersistedState'
import { BenchSection } from './BenchSection'
import { pickScore, pickTotalsLine } from './blindPickText'
import {
  STAGE_LABELS, arenaGroups, compareProblem, formatCost, formatLatency, formatScore, formatWhen, judgeRunLine, runConfigLabel,
  plainError, runExtraLabel, runStatusLabel, runStatusTone, stageLabel, toggleCompare,
} from './benchmarkForm'
import { bestValueRunIds, runQualityRanks, sortRuns, type SortKey } from './runRanking'
import { SortBar } from './SortBar'

type Props = {
  runs: BenchmarkRun[]
  stageFilter: string
  onStageFilter: (stage: string) => void
  maxCompare: number
  phone: boolean
  onCompare: (runIds: number[]) => void
  onOpen: (runId: number) => void
  /** False on a remote viewer: deleting a run is PC only. */
  canDelete: boolean
  /** A run was deleted: reload the list. */
  onDeleted: (runId: number) => void
}

/** Recent runs: tick 2-4 to compare in the Arena; runs started together compare in one click. */
export function RunsCard({ runs, stageFilter, onStageFilter, maxCompare, phone, onCompare, onOpen, canDelete, onDeleted }: Props) {
  const [picked, setPicked] = useState<number[]>([])
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [storedSort, setSort] = usePersistedState<string>('benchmark.runsSort', 'quality')
  const sort: SortKey = storedSort === 'speed' || storedSort === 'cost' ? storedSort : 'quality'
  const ranks = runQualityRanks(runs)
  const ranking: Ranking = { ranks, bestValue: bestValueRunIds(runs) }
  const sorted = sortRuns(runs, sort, ranks)
  // Only runs still listed stay picked (a stage filter change hides some).
  const selected = picked.filter((id) => runs.some((r) => r.id === id))
  const problem = compareProblem(selected, runs, maxCompare)
  const groups = arenaGroups(runs, maxCompare).slice(0, 5)
  const toggle = (id: number) => setPicked(toggleCompare(selected, id, maxCompare))
  const remove = (id: number) => {
    setDeletingId(id)
    setDeleteError(null)
    deleteBenchmarkRun(id).then(
      () => {
        setDeletingId(null)
        setPicked((cur) => cur.filter((x) => x !== id))
        onDeleted(id)
      },
      (e: unknown) => {
        setDeletingId(null)
        setDeleteError(plainError(e, { pcOnly: true }))
      },
    )
  }
  const del = (r: BenchmarkRun) => canDelete && (
    <ConfirmButton size="sm" name={`run ${r.id}`} busy={deletingId === r.id} disabled={deletingId !== null && deletingId !== r.id} onConfirm={() => remove(r.id)} />
  )

  const filter = (
    <label className="bench-inline-field">
      <span className="visually-hidden">Show runs of</span>
      <select aria-label="Show runs of" value={stageFilter} onChange={(e) => onStageFilter(e.target.value)}>
        <option value="">All stages</option>
        {Object.entries(STAGE_LABELS).map(([k, v]) => (
          <option key={k} value={k}>{v}</option>
        ))}
      </select>
    </label>
  )

  return (
    <BenchSection id="runs" meta={runs.length ? `${runs.length} shown, best ${sort} first` : undefined} actions={filter} className="bench-runs">
      {runs.length === 0 ? (
        <p className="muted">No runs yet. Estimate and start one above; every run is kept here with its score.</p>
      ) : (
        <>
          {groups.length > 0 && (
            <ul className="bench-groups" aria-label="Model Arena runs">
              {groups.map((g) => (
                <li key={g.group}>
                  <span>
                    <strong>{g.label}</strong>{' '}
                    <span className="muted">· {g.runIds.length} engines · {formatWhen(g.created)}</span>
                  </span>
                  <button type="button" className={buttonClass('secondary', 'sm')} aria-label={`Compare ${g.label} in Arena`} onClick={() => onCompare(g.runIds)}>
                    Compare
                  </button>
                </li>
              ))}
            </ul>
          )}
          <SortBar name="runs" value={sort} onChange={setSort} />
          <p className="muted bench-rank-note">
            Quality rank is by score within each stage (1 = best); a run with no score has none. Best value: the most score per dollar among a stage&apos;s top runs.
          </p>
          {phone ? (
            <RunCards runs={sorted} ranking={ranking} selected={selected} onToggle={toggle} onOpen={onOpen} del={del} />
          ) : (
            <RunTable runs={sorted} ranking={ranking} selected={selected} onToggle={toggle} onOpen={onOpen} del={del} />
          )}
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={!!problem} onClick={() => onCompare(selected)}>
              Compare in Arena{selected.length ? ` (${selected.length})` : ''}
            </button>
            {problem && <span className="muted" data-testid="compare-reason">{problem}</span>}
          </div>
          {deleteError && (
            <p className="error" role="alert">
              {deleteError}
            </p>
          )}
        </>
      )}
    </BenchSection>
  )
}

type Ranking = { ranks: Map<number, number>; bestValue: Set<number> }

type ListProps = {
  runs: BenchmarkRun[]
  ranking: Ranking
  selected: number[]
  onToggle: (id: number) => void
  onOpen: (id: number) => void
  del: (r: BenchmarkRun) => ReactNode
}

const BestValue = () => <Badge tone="ok" title="Most score per dollar among the top runs of its stage">Best value</Badge>

const runName = (r: BenchmarkRun) => r.label || runConfigLabel(r)

function ScoreCell({ r }: { r: BenchmarkRun }) {
  const judged = r.judge?.average?.overall
  const picked = pickScore(r.picks)
  return (
    <>
      {picked != null && (
        <span className="num" title={pickTotalsLine(r.picks)}>
          your picks {formatScore(picked)}
          <span className="muted"> · auto </span>
        </span>
      )}
      <span className={picked != null ? 'muted num' : 'num'}>{formatScore(r.aggregate_score)}</span>
      {judged != null && (
        <span className="muted num bench-judge-score" title={judgeRunLine(r.judge)}>
          {' '}
          · judge {formatScore(judged)}
        </span>
      )}
    </>
  )
}

function RunTable({ runs, ranking, selected, onToggle, onOpen, del }: ListProps) {
  return (
    <div className="table-scroll scroll-box">
      <table className="bench-table bench-run-table" aria-label="Runs">
        <thead>
          <tr>
            <th><span className="visually-hidden">Compare</span></th>
            <th>Run</th>
            <th>Engine</th>
            <th>Prompt</th>
            <th>Status</th>
            <th className="num">Quality rank</th>
            <th className="num">Score</th>
            <th className="num">Passed</th>
            <th className="num">Avg time</th>
            <th className="num">Cost</th>
            <th>Started</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className={selected.includes(r.id) ? 'selected' : undefined}>
              <td>
                <input type="checkbox" aria-label={`Compare run ${r.id} ${runName(r)}`} checked={selected.includes(r.id)} onChange={() => onToggle(r.id)} />
              </td>
              <td>
                {runExtraLabel(r) || <span className="muted">Run {r.id}</span>}
                <div className="muted">{stageLabel(r.stage)}{r.arena_group ? ' · arena' : ''}</div>
              </td>
              <td>{runConfigLabel(r)}</td>
              <td>{r.prompt_version || <span className="muted">—</span>}</td>
              <td>
                <Badge tone={runStatusTone(r.status)}>{runStatusLabel(r.status)}</Badge>
                {r.note && <div className="muted bench-note-text">{r.note}</div>}
              </td>
              <td className="num">{ranking.ranks.get(r.id) ?? <span className="muted">—</span>}</td>
              <td className="num"><ScoreCell r={r} /></td>
              <td className="num">
                {r.passed_count}/{r.scored_count}
                {r.error_count ? <div className="muted">{r.error_count} errors</div> : null}
              </td>
              <td className="num">{formatLatency(r.avg_latency_seconds)}</td>
              <td className="num">
                {formatCost(r.total_cost_usd)}
                {r.judge && <div className="muted">+ {formatCost(r.judge.cost_usd)} judge</div>}
                {ranking.bestValue.has(r.id) && <div><BestValue /></div>}
              </td>
              <td className="num">{formatWhen(r.created_at)}</td>
              <td className="bench-cell-action">
                <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Results of run ${r.id}`} onClick={() => onOpen(r.id)}>
                  Results
                </button>
                {del(r)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RunCards({ runs, ranking, selected, onToggle, onOpen, del }: ListProps) {
  return (
    <ul className="bench-run-cards scroll-box" aria-label="Runs">
      {runs.map((r) => (
        <li key={r.id} className={selected.includes(r.id) ? 'selected' : undefined}>
          <label className="bench-run-pick">
            <input type="checkbox" checked={selected.includes(r.id)} onChange={() => onToggle(r.id)} aria-label={`Compare run ${r.id} ${runName(r)}`} />
            <span>
              <strong>{runName(r)}</strong>
              {runExtraLabel(r) && <span className="muted"> · {runConfigLabel(r)}</span>}
            </span>
          </label>
          <p className="pill-row">
            <Badge tone={runStatusTone(r.status)}>{runStatusLabel(r.status)}</Badge>
            <Badge tone="neutral">{stageLabel(r.stage)}</Badge>
            {ranking.ranks.has(r.id) && <Badge tone="accent">Quality rank {ranking.ranks.get(r.id)}</Badge>}
            {ranking.bestValue.has(r.id) && <BestValue />}
            {r.prompt_version && <Badge tone="neutral">{r.prompt_version}</Badge>}
          </p>
          <p className="num">
            Score <strong><ScoreCell r={r} /></strong> · {r.passed_count}/{r.scored_count} passed{r.error_count ? ` · ${r.error_count} errors` : ''} · {formatLatency(r.avg_latency_seconds)} ·{' '}
            {formatCost(r.total_cost_usd)}
            {r.judge ? ` (+ ${formatCost(r.judge.cost_usd)} judge)` : ''}
          </p>
          {r.note && <p className="muted">{r.note}</p>}
          <div className="bench-run-card-foot">
            <span className="muted num">{formatWhen(r.created_at)}</span>
            <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Results of run ${r.id}`} onClick={() => onOpen(r.id)}>
              Results
            </button>
            {del(r)}
          </div>
        </li>
      ))}
    </ul>
  )
}
