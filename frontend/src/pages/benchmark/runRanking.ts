/** Ranking, sorting and the "best value" hint shared by the Recent runs tables. */
import type { BenchmarkRun } from '../../types/benchmark'

export type SortKey = 'quality' | 'speed' | 'cost'

export const SORT_LABELS: Record<SortKey, string> = { quality: 'Quality', speed: 'Speed', cost: 'Cost' }

const finite = (n: number | null | undefined): n is number => n != null && Number.isFinite(n)

/** Competition ranks (1, 2, 2, 4): equal figures share a rank; a missing figure gets none. */
export function competitionRanks(values: (number | null | undefined)[], higherIsBetter: boolean): (number | null)[] {
  const sign = higherIsBetter ? -1 : 1
  return values.map((v) => (finite(v) ? 1 + values.filter((o) => finite(o) && sign * (o - v) < 0).length : null))
}

/** Row order for a sort: the best first, rows with no figure last, ties keep their original order. */
export function sortedOrder(ranks: (number | null)[]): number[] {
  return ranks
    .map((r, i) => ({ r, i }))
    .sort((a, b) => (a.r ?? Infinity) - (b.r ?? Infinity) || a.i - b.i)
    .map((x) => x.i)
}

export interface ValueInput {
  /** Higher is better; null when the row has no quality figure. */
  quality: number | null
  /** Dollars; rows with none or a free row are not considered. */
  cost: number | null
}

/**
 * The row with the most quality per dollar among those within `tolerance` of the best quality; -1 when none.
 * Restricting to the top group keeps a very cheap but clearly worse row from winning.
 */
export function bestValueIndex(rows: ValueInput[], tolerance: number): number {
  const qualities = rows.filter((r) => finite(r.quality) && finite(r.cost) && r.cost > 0).map((r) => r.quality as number)
  if (qualities.length === 0) return -1
  const top = Math.max(...qualities)
  let best = -1
  let bestValue = -Infinity
  rows.forEach((r, i) => {
    if (!finite(r.quality) || !finite(r.cost) || r.cost <= 0 || r.quality < top - tolerance) return
    const value = r.quality / r.cost
    if (value > bestValue) {
      bestValue = value
      best = i
    }
  })
  return best
}

/** Score gap (on the 0-1 scale) within which a run still counts as top quality for the value hint. */
export const RUN_VALUE_TOLERANCE = 0.02

/** A run with a score of its own: finished, and at least one case was scored. */
const scoredRun = (r: BenchmarkRun) => r.status === 'done' && r.scored_count > 0 && finite(r.aggregate_score)

/** Quality rank of each run within its stage (scores of different stages are not comparable); unscored runs get none. */
export function runQualityRanks(runs: BenchmarkRun[]): Map<number, number> {
  const out = new Map<number, number>()
  for (const stage of new Set(runs.map((r) => r.stage ?? ''))) {
    const inStage = runs.filter((r) => (r.stage ?? '') === stage && scoredRun(r))
    const ranks = competitionRanks(inStage.map((r) => r.aggregate_score), true)
    inStage.forEach((r, i) => {
      const rank = ranks[i]
      if (rank != null) out.set(r.id, rank)
    })
  }
  return out
}

/** Ids of the best-value run of each stage. */
export function bestValueRunIds(runs: BenchmarkRun[]): Set<number> {
  const out = new Set<number>()
  for (const stage of new Set(runs.map((r) => r.stage ?? ''))) {
    const inStage = runs.filter((r) => (r.stage ?? '') === stage && scoredRun(r))
    const i = bestValueIndex(inStage.map((r) => ({ quality: r.aggregate_score, cost: r.total_cost_usd })), RUN_VALUE_TOLERANCE)
    if (i >= 0) out.add(inStage[i].id)
  }
  return out
}

/** The runs ordered best first by the chosen figure; the first listed order breaks ties. Unmeasured runs go last. */
export function sortRuns(runs: BenchmarkRun[], key: SortKey, qualityRanks: Map<number, number>): BenchmarkRun[] {
  const figure = (r: BenchmarkRun): number | null => {
    if (key === 'quality') return qualityRanks.get(r.id) ?? null
    return (key === 'speed' ? r.avg_latency_seconds : r.total_cost_usd) ?? null
  }
  const ranks = key === 'quality' ? runs.map(figure) : competitionRanks(runs.map(figure), false)
  return sortedOrder(ranks).map((i) => runs[i])
}
