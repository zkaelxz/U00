/** Folds recorded Benchmark Lab runs into one row per model, with speed and quality ranks. */
import type { BenchmarkRun } from '../../types/benchmark'
import { configLabel } from './benchmarkForm'

export interface ModelSummaryRow {
  key: string
  stage: string
  name: string
  runs: number
  /** Mean score (0-1) over runs that scored at least one case; null when none did. */
  score: number | null
  /** Mean seconds per case over runs that report one; null when none did. */
  latency: number | null
  /** Mean cost of one run; null when no run reports it. */
  cost: number | null
  /** 1 = best within the stage; null when this model has no figure to rank. */
  qualityRank: number | null
  speedRank: number | null
}

const mean = (xs: number[]): number | null => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
const finite = (n: number | null | undefined): n is number => n != null && Number.isFinite(n)

/** Competition ranking (1, 2, 2, 4): equal figures share a rank. */
function rankBy(rows: ModelSummaryRow[], value: (r: ModelSummaryRow) => number | null, lowerIsBetter: boolean): Map<string, number> {
  const ranked = rows.filter((r) => value(r) != null)
  const sign = lowerIsBetter ? 1 : -1
  const out = new Map<string, number>()
  for (const r of ranked) {
    const v = value(r) as number
    out.set(r.key, 1 + ranked.filter((o) => sign * ((value(o) as number) - v) < 0).length)
  }
  return out
}

/** Finished runs only: a failed or stopped run's partial figures would skew a model's average. */
export function summariseModels(runs: BenchmarkRun[]): ModelSummaryRow[] {
  const groups = new Map<string, BenchmarkRun[]>()
  for (const r of runs) {
    if (r.status !== 'done' || !r.engine) continue
    const key = `${r.stage}|${r.engine}|${r.model ?? ''}`
    groups.set(key, [...(groups.get(key) ?? []), r])
  }
  const rows: ModelSummaryRow[] = [...groups].map(([key, rs]) => ({
    key,
    stage: rs[0].stage ?? '',
    name: configLabel(rs[0].stage, rs[0].engine, rs[0].model),
    runs: rs.length,
    score: mean(rs.filter((r) => r.scored_count > 0).map((r) => r.aggregate_score).filter(finite)),
    latency: mean(rs.map((r) => r.avg_latency_seconds).filter(finite)),
    cost: mean(rs.map((r) => r.total_cost_usd).filter(finite)),
    qualityRank: null,
    speedRank: null,
  }))
  // Scores from different stages (CER vs similarity) are not comparable, so ranks stay inside a stage.
  for (const stage of new Set(rows.map((r) => r.stage))) {
    const inStage = rows.filter((r) => r.stage === stage)
    const quality = rankBy(inStage, (r) => r.score, false)
    const speed = rankBy(inStage, (r) => r.latency, true)
    for (const r of inStage) {
      r.qualityRank = quality.get(r.key) ?? null
      r.speedRank = speed.get(r.key) ?? null
    }
  }
  return rows.sort((a, b) => a.stage.localeCompare(b.stage) || (a.qualityRank ?? 99) - (b.qualityRank ?? 99) || a.name.localeCompare(b.name))
}
