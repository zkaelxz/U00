// Pure formatting for a job's "Time by stage" breakdown (Job history).
// Stage names are fixed labels from Baihe's own code, shown as they come.
import type { JobStageRun } from '../../types/jobs'

/** A stage length: "3.4 s" under 10 s, "42 s", "1 min 12 s", "2 min", "1 h 05 min". */
export function formatStageDuration(seconds: number): string {
  const s = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  if (s < 10) {
    const tenths = Math.round(s * 10) / 10
    if (tenths < 10) return `${tenths === 0 ? '0' : tenths.toFixed(1)} s`
  }
  const total = Math.round(s)
  if (total < 60) return `${total} s`
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const sec = total % 60
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`
  return sec ? `${m} min ${sec} s` : `${m} min`
}

/** Estimated spend: "$0.03", "under $0.01", or null when there was none. */
export function formatStageCost(usd: number): string | null {
  if (!Number.isFinite(usd) || usd <= 0) return null
  if (usd < 0.005) return 'under $0.01'
  return `$${usd.toFixed(2)}`
}

type StageRow = { key: string; name: string; at: string; duration: string; cost: string | null; share: number }

/** A stage's start as a clock time in this browser ("14:05:03"), empty for a bad value. */
export function stageClock(startedAt: number): string {
  const d = new Date(startedAt * 1000)
  return Number.isFinite(d.getTime()) ? d.toLocaleTimeString(undefined, { hour12: false }) : ''
}

/** The run's total length: its own total, or the stages' sum when that is missing. */
function runSeconds(run: JobStageRun): number {
  const sum = run.stages.reduce((a, st) => a + Math.max(0, st.duration_seconds || 0), 0)
  return run.total_seconds > 0 ? Math.max(run.total_seconds, sum) : sum
}

/**
 * One row per stage, in the order the job ran them. `share` is the stage's
 * percentage (0-100) of the run's total, for the bar's width.
 */
export function stageRows(run: JobStageRun): StageRow[] {
  const total = runSeconds(run)
  return run.stages.map((st, i) => {
    const d = Math.max(0, st.duration_seconds || 0)
    return {
      key: `${i}-${st.stage}`,
      name: st.stage || 'Unnamed stage',
      at: stageClock(st.started_at),
      duration: formatStageDuration(d),
      cost: formatStageCost(st.cost_usd),
      share: total > 0 ? Math.min(100, Math.round((d / total) * 1000) / 10) : 0,
    }
  })
}

/** "Total 3 min 5 s · estimated $0.05", with "so far" while the run is still going. */
export function stageTotalLine(run: JobStageRun): string {
  const parts = [`Total ${formatStageDuration(runSeconds(run))}${run.running ? ' so far' : ''}`]
  const cost = formatStageCost(run.cost_usd)
  if (cost) parts.push(`estimated ${cost}`)
  return parts.join(' · ')
}

/** "Latest of 3 runs." when the job ran more than once, else null. */
export function runsNote(count: number): string | null {
  return count > 1 ? `Latest of ${count} runs.` : null
}

export const NO_STAGE_TIMING = 'No stage timing recorded for this job.'
export const STAGE_TIMING_UNAVAILABLE = 'Stage timing unavailable.'

// Names for the stages a transcription run records (stage_seconds in its result); a stage the run
// did not record is left out, never filled in.
const RECORDED_STAGE_NAMES: Array<[string, string]> = [
  ['separate', 'Vocal separation'],
  ['load', 'Loading the speech model'],
  ['decode_vad', 'Decoding audio and finding speech'],
  ['transcribe', 'Speech recognition'],
  ['align', 'Alignment and splitting'],
]

/** Rows for the stages a finished transcription recorded, in run order; [] when it recorded none. */
export function recordedStageRows(result: Record<string, unknown> | null | undefined): Array<{ key: string; name: string; duration: string; share: number }> {
  const raw = result?.stage_seconds
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  const seconds = raw as Record<string, unknown>
  const known = RECORDED_STAGE_NAMES.flatMap(([key, name]) => {
    const v = seconds[key]
    return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? [{ key, name, v }] : []
  })
  const total = known.reduce((a, r) => a + r.v, 0)
  return known.map((r) => ({ key: r.key, name: r.name, duration: formatStageDuration(r.v), share: total > 0 ? Math.round((r.v / total) * 1000) / 10 : 0 }))
}
