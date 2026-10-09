import { useState } from 'react'

import type { BenchmarkRun } from '../../api/benchmark'
import { Badge } from '../../components/Badge'
import { buttonClass } from '../../components/uiClasses'
import { BenchSection } from './BenchSection'
import {
  STAGE_LABELS, arenaGroups, compareProblem, formatCost, formatLatency, formatScore, formatWhen, runConfigLabel,
  runStatusLabel, runStatusTone, stageLabel, toggleCompare,
} from './benchmarkForm'

type Props = {
  runs: BenchmarkRun[]
  stageFilter: string
  onStageFilter: (stage: string) => void
  maxCompare: number
  phone: boolean
  onCompare: (runIds: number[]) => void
  onOpen: (runId: number) => void
}

/** Recent runs: tick 2-4 to compare in the Arena; runs started together compare in one click. */
export function RunsCard({ runs, stageFilter, onStageFilter, maxCompare, phone, onCompare, onOpen }: Props) {
  const [picked, setPicked] = useState<number[]>([])
  // Only runs still listed stay picked (a stage filter change hides some).
  const selected = picked.filter((id) => runs.some((r) => r.id === id))
  const problem = compareProblem(selected, runs, maxCompare)
  const groups = arenaGroups(runs, maxCompare).slice(0, 5)
  const toggle = (id: number) => setPicked(toggleCompare(selected, id, maxCompare))

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
    <BenchSection id="runs" meta={runs.length ? `${runs.length} shown, newest first` : undefined} actions={filter} className="bench-runs">
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
          {phone ? (
            <RunCards runs={runs} selected={selected} onToggle={toggle} onOpen={onOpen} />
          ) : (
            <RunTable runs={runs} selected={selected} onToggle={toggle} onOpen={onOpen} />
          )}
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={!!problem} onClick={() => onCompare(selected)}>
              Compare in Arena{selected.length ? ` (${selected.length})` : ''}
            </button>
            {problem && <span className="muted" data-testid="compare-reason">{problem}</span>}
          </div>
        </>
      )}
    </BenchSection>
  )
}

type ListProps = { runs: BenchmarkRun[]; selected: number[]; onToggle: (id: number) => void; onOpen: (id: number) => void }

const runName = (r: BenchmarkRun) => r.label || runConfigLabel(r)

function ScoreCell({ r }: { r: BenchmarkRun }) {
  return <span className="num">{formatScore(r.aggregate_score)}</span>
}

function RunTable({ runs, selected, onToggle, onOpen }: ListProps) {
  return (
    <div className="table-scroll">
      <table className="bench-table bench-run-table" aria-label="Runs">
        <thead>
          <tr>
            <th><span className="visually-hidden">Compare</span></th>
            <th>Run</th>
            <th>Engine</th>
            <th>Prompt</th>
            <th>Status</th>
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
                {r.label || <span className="muted">Run {r.id}</span>}
                <div className="muted">{stageLabel(r.stage)}{r.arena_group ? ' · arena' : ''}</div>
              </td>
              <td>{runConfigLabel(r)}</td>
              <td>{r.prompt_version || <span className="muted">—</span>}</td>
              <td>
                <Badge tone={runStatusTone(r.status)}>{runStatusLabel(r.status)}</Badge>
                {r.note && <div className="muted bench-note-text">{r.note}</div>}
              </td>
              <td className="num"><ScoreCell r={r} /></td>
              <td className="num">{r.passed_count}/{r.scored_count}</td>
              <td className="num">{formatLatency(r.avg_latency_seconds)}</td>
              <td className="num">{formatCost(r.total_cost_usd)}</td>
              <td className="num">{formatWhen(r.created_at)}</td>
              <td className="bench-cell-action">
                <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Results of run ${r.id}`} onClick={() => onOpen(r.id)}>
                  Results
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RunCards({ runs, selected, onToggle, onOpen }: ListProps) {
  return (
    <ul className="bench-run-cards" aria-label="Runs">
      {runs.map((r) => (
        <li key={r.id} className={selected.includes(r.id) ? 'selected' : undefined}>
          <label className="bench-run-pick">
            <input type="checkbox" checked={selected.includes(r.id)} onChange={() => onToggle(r.id)} aria-label={`Compare run ${r.id} ${runName(r)}`} />
            <span>
              <strong>{runName(r)}</strong>
              <span className="muted"> · {runConfigLabel(r)}</span>
            </span>
          </label>
          <p className="pill-row">
            <Badge tone={runStatusTone(r.status)}>{runStatusLabel(r.status)}</Badge>
            <Badge tone="neutral">{stageLabel(r.stage)}</Badge>
            {r.prompt_version && <Badge tone="neutral">{r.prompt_version}</Badge>}
          </p>
          <p className="num">
            Score <strong><ScoreCell r={r} /></strong> · {r.passed_count}/{r.scored_count} passed · {formatLatency(r.avg_latency_seconds)} ·{' '}
            {formatCost(r.total_cost_usd)}
          </p>
          {r.note && <p className="muted">{r.note}</p>}
          <div className="bench-run-card-foot">
            <span className="muted num">{formatWhen(r.created_at)}</span>
            <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Results of run ${r.id}`} onClick={() => onOpen(r.id)}>
              Results
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}
