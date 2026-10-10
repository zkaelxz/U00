import { formatElapsed } from './autotuneGlossary'
import type { RawStatus, SpeechCoverageReport } from '../../../types/speechCoverage'

export const DEFAULT_MIN_GAP_SECONDS = 2

// Where the audio can start playing: a moment before the gap so its first word is heard.
export const PLAY_LEAD_SECONDS = 0.5

export function rawStatusText(status: RawStatus): string {
  if (status === 'lost_after') return 'Whisper produced text here but it was lost afterwards'
  if (status === 'none') return 'Whisper produced nothing here'
  return 'No raw transcript to compare with'
}

export function gapRange(start: number, end: number): string {
  return `${formatElapsed(start)}–${formatElapsed(end)}`
}

export function coverageSummary(report: SpeechCoverageReport): string {
  if (report.failed_reason) return 'check failed'
  if (report.covered_percent === null) return 'no speech found'
  return `${report.covered_percent}% of speech covered · ${report.gaps_total} gap${report.gaps_total === 1 ? '' : 's'}`
}

export function coverageTotals(report: SpeechCoverageReport): string {
  const speech = report.speech_seconds ?? 0
  const covered = report.covered_seconds ?? 0
  if (!speech) return 'No speech was found in the audio.'
  return `${formatElapsed(covered)} of ${formatElapsed(speech)} of speech has a line (${report.covered_percent}%).`
}

export function coverageBlocker(hasAudio: boolean, busy: boolean): string | null {
  if (busy) return 'Wait for the running job to finish.'
  if (!hasAudio) return 'Still needed: audio on this title.'
  return null
}

export function failedText(report: SpeechCoverageReport): string {
  if (report.failed_reason === 'unreadable') return "The audio's length couldn't be read, so it wasn't checked."
  if (report.failed_reason === 'decode') return 'The audio could not be decoded for the check.'
  return 'The check failed. Details are in the app log.'
}
