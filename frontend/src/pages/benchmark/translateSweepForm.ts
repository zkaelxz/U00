// The translation sweep card's pure helpers: what it may start and how its steps read. The shared status,
// recommendation and canDecide wording lives in sweepForm; scores here are chrF, so a difference is in chrF points.
import type { BenchmarkSet, SweepStep, TranslateSweepEstimate } from '../../types/benchmark'
import { formatCost } from './benchmarkForm'

/** Golden sets that hold translation cases with a reference, by name. */
export function translateSweepSets(sets: BenchmarkSet[]): string[] {
  const names = sets
    .filter((s) => s.stage === 'translation' && s.set_name && s.with_reference > (s.excluded ?? 0))
    .map((s) => s.set_name)
  return [...new Set(names)].sort((a, b) => a.localeCompare(b))
}

export const translateSweepKey = (setName: string, language: string, engine: string, model: string, series: number | null) =>
  `${setName}|${language}|${engine}|${model}|${series ?? ''}`

/** A score difference as chrF points: "2.0 chrF points better", "0.5 chrF points worse", "no change". */
export function chrfPoints(delta: number | null | undefined): string {
  if (delta == null || !Number.isFinite(delta)) return '—'
  const pts = Math.round(delta * 1000) / 10
  if (pts === 0) return 'no change'
  return `${Math.abs(pts).toFixed(1)} chrF points ${pts > 0 ? 'better' : 'worse'}`
}

export function translateStartLabel(est: TranslateSweepEstimate | null): string {
  return est && est.passes_done > 0 ? 'Resume sweep' : 'Start sweep'
}

/** What the estimate says about money, in one line. */
export function costLine(est: TranslateSweepEstimate): string {
  if (!est.cap_applies) return `Cost ${formatCost(est.estimated_cost_usd)}: this engine is free to use, so the monthly cap doesn't apply.`
  const left = est.remaining_usd == null ? '' : ` of the ${formatCost(est.remaining_usd)} left this month`
  return `Cost up to ${formatCost(est.estimated_cost_usd)}${left}. Every pass is also held to the monthly cap while it runs.`
}

/** Why Start is off, or null when it may start. */
export function translateStartProblem(opts: {
  setName: string
  engine: string
  estimate: TranslateSweepEstimate | null
  estimateFresh: boolean
  running: boolean
}): string | null {
  if (opts.running) return 'A benchmark run is going. Wait for it to finish.'
  if (!opts.setName) return 'Pick a golden set with translation cases.'
  if (!opts.engine) return 'Pick a translation engine.'
  if (!opts.estimate || !opts.estimateFresh) return 'Estimate this sweep first.'
  if (opts.estimate.monthly_refusal) return opts.estimate.monthly_refusal
  if (opts.estimate.estimate_above_cap) {
    return `This sweep is estimated at ${formatCost(opts.estimate.estimated_cost_usd)}, more than the ${formatCost(opts.estimate.remaining_usd)} left of this month's cap.`
  }
  if (opts.estimate.passes_to_run === 0) return 'Every pass of this sweep has already run. Its result is below.'
  return null
}

const STEP_NAMES: Record<string, string> = {
  noise: 'Default (noise)',
  model: 'Model',
  holdout: 'Hold-out',
  batch_size: 'Batch size',
  context_window: 'Context lines before',
  context_window_ahead: 'Context lines after',
  reflect: 'Reflect',
  style_preset: 'Style preset',
}

function valueText(value: SweepStep['value']): string {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return String(value).replace(/_/g, ' ')
}

/** "Batch size 15", "Model claude-haiku", "Hold-out: winner", "Default (noise)". */
export function translateStepLabel(step: SweepStep): string {
  const name = STEP_NAMES[step.step] ?? step.step
  if (step.step === 'noise') return name
  if (step.step === 'holdout') return `${name}: ${valueText(step.value)}`
  return `${name} ${valueText(step.value)}`
}
