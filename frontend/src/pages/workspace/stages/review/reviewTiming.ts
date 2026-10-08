import type { SpeakerTimeSummary } from '../../../../types/workspace'

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${(seconds - m * 60).toFixed(2).padStart(5, '0')}`
}

// Whole seconds for a media duration ("24:10", "1:02:03").
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '–'
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** One line per speaker, e.g. "Anna  3:40 · 62% · 41 turns", biggest first. */
export function speakerTimeLines(s: SpeakerTimeSummary): string[] {
  return s.speakers.map((x) => `${x.label}  ${formatDuration(x.seconds)} · ${x.percent}% · ${x.turns} turn${x.turns === 1 ? '' : 's'}`)
}

/** Footer for the speaker time list: total speech and audio no turn covers. */
export function speakerTimeFooter(s: SpeakerTimeSummary): string {
  const gap = s.uncovered_seconds === null ? '' : `; ${formatDuration(s.uncovered_seconds)} of the audio has no speaker turn`
  return `${formatDuration(s.total_speech_seconds)} of speech in the saved detection${gap}.`
}
