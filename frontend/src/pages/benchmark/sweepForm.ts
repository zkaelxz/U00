// The settings sweep card's pure helpers: what it may start, and how a sweep and its
// recommendation read. Scores are 1 − CER, so a score difference is a CER difference with the sign flipped.
import type { BadgeTone } from '../../components/labels'
import type { BenchmarkSet, Sweep, SweepEstimate, SweepLanguage, SweepStatus, SweepStep, SweepValue } from '../../types/benchmark'

export const SWEEP_LANGUAGES: { id: SweepLanguage; label: string }[] = [
  { id: 'zh', label: 'Chinese' },
  { id: 'ja', label: 'Japanese' },
  { id: 'ko', label: 'Korean' },
]

/** Golden sets that hold transcription clips, by name. */
export function sweepSets(sets: BenchmarkSet[]): string[] {
  const names = sets.filter((s) => s.stage === 'transcription' && s.set_name && s.case_count > (s.excluded ?? 0)).map((s) => s.set_name)
  return [...new Set(names)].sort((a, b) => a.localeCompare(b))
}

export const sweepKey = (setName: string, language: string) => `${setName}|${language}`

/** "about 3 h 20 min", "about 12 min", "under a minute". */
export function formatSweepTime(seconds: number): string {
  if (seconds < 60) return 'under a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `about ${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `about ${h} h ${m} min` : `about ${h} h`
}

/** A score difference as CER points: "2.0 CER points better", "0.5 CER points worse", "no change". */
export function cerPoints(delta: number | null | undefined): string {
  if (delta == null || !Number.isFinite(delta)) return '—'
  const pts = Math.round(delta * 1000) / 10
  if (pts === 0) return 'no change'
  return `${Math.abs(pts).toFixed(1)} CER points ${pts > 0 ? 'better' : 'worse'}`
}

/** What Start says: Resume once a sweep of this selection has finished passes to skip. */
export function startLabel(est: SweepEstimate | null): string {
  return est && est.passes_done > 0 ? 'Resume sweep' : 'Start sweep'
}

/** Why Start is off, or null when it may start. */
export function startProblem(opts: {
  setName: string
  estimate: SweepEstimate | null
  estimateFresh: boolean
  running: boolean
}): string | null {
  if (opts.running) return 'A benchmark run is going. Wait for it to finish.'
  if (!opts.setName) return 'Pick a golden set with audio clips.'
  if (!opts.estimate || !opts.estimateFresh) return 'Estimate this sweep first.'
  if (opts.estimate.monthly_refusal) return opts.estimate.monthly_refusal
  if (opts.estimate.passes_to_run === 0) return 'Every pass of this sweep has already run. Its result is below.'
  return null
}

export function sweepStatusLabel(status: SweepStatus): string {
  return {
    queued: 'Waiting for the GPU', running: 'Running', done: 'Finished', cancelled: 'Stopped',
    failed: 'Failed', interrupted: 'Interrupted',
  }[status]
}

export function sweepStatusTone(status: SweepStatus): BadgeTone {
  if (status === 'done') return 'ok'
  if (status === 'failed') return 'bad'
  if (status === 'cancelled' || status === 'interrupted') return 'warn'
  return 'accent'
}

export function recommendationTone(state: string): BadgeTone {
  return state === 'adopt' ? 'ok' : state === 'not_confirmed' ? 'warn' : 'neutral'
}

export function recommendationLabel(state: string): string {
  return state === 'adopt' ? 'Change recommended' : state === 'not_confirmed' ? 'Not confirmed' : 'Keep the default'
}

/** Approve needs a winner that held up on the hold-out; reject needs any winner other than the default. */
export function canDecide(sweep: Sweep | null): { approve: boolean; reject: boolean } {
  const rec = sweep?.status === 'done' ? sweep.recommendation : null
  const open = !!rec && !sweep?.decision && rec.state !== 'keep_default'
  return { approve: open && rec?.state === 'adopt', reject: open }
}

function valueText(value: SweepValue | string | null): string {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return String(value)
}

const STEP_LABELS: Record<string, string> = {
  noise: 'Default (noise)',
  model: 'Model',
  holdout: 'Hold-out',
  beam_size: 'Beam size',
  min_silence_ms: 'Minimum silence (ms)',
  vad_threshold: 'Voice detection threshold',
  hallucination_silence_sec: 'Skip silences longer than (s)',
  difficult_audio: 'Difficult audio',
  whisper_repeat_guard: 'Repeat guard',
  separate_vocals_first: 'Separate vocals first',
}

/** "Beam size 3", "Model large-v3-turbo", "Hold-out: winner", "Default (noise)". */
export function stepLabel(step: SweepStep): string {
  const name = STEP_LABELS[step.step] ?? step.step
  if (step.step === 'noise') return name
  if (step.step === 'holdout') return `${name}: ${valueText(step.value)}`
  return `${name} ${valueText(step.value)}`
}

/** The passes of one sweep: "12 run, 4 reused from earlier runs". */
export function passesLine(sweep: Sweep): string {
  const reused = sweep.passes_reused ? `, ${sweep.passes_reused} reused from earlier runs` : ''
  return `${sweep.passes_run} ${sweep.passes_run === 1 ? 'pass' : 'passes'} run${reused}`
}
