import type { BenchmarkPickSummary, BenchmarkPickTotals } from '../../types/benchmark'

/** "You picked this run 3 times, the other 1, 1 tie" for one run's totals. */
export function pickTotalsLine(p: BenchmarkPickTotals | null | undefined): string {
  if (!p) return ''
  const parts = [`Your picks: ${p.won} won`, `${p.lost} lost`]
  if (p.tie) parts.push(`${p.tie} tied`)
  return parts.join(' · ')
}

/** Totals for a pair, and how often the judge's higher score matched the owner's choice. */
export function pickSummaryLine(s: BenchmarkPickSummary): string {
  const parts = [`Picked ${s.picked} of ${s.total}: first run ${s.a_wins}, second run ${s.b_wins}, tied ${s.ties}`]
  const decided = s.judge_agrees + s.judge_disagrees
  if (decided) parts.push(`the judge agreed with you on ${s.judge_agrees} of ${decided}`)
  return parts.join(' · ')
}
