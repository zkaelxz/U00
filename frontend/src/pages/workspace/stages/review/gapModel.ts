import type { TranscribeGap } from '../../../../types/workspace'

export const GAP_SPEECH_TEXT = 'No line here: speech detected'
export const GAP_PLAIN_TEXT = 'No line here'

// `speech` is true only when the speech coverage check heard speech there, and
// null when it has not run, so a gap is never claimed to be silent.
export const gapText = (g: Pick<TranscribeGap, 'speech'>): string => (g.speech ? GAP_SPEECH_TEXT : GAP_PLAIN_TEXT)

// Spoken name of the chip: which gap you are on (# before the first jump) out of how many.
// The phone's plain-words version of the chip: no number box to squeeze into a pill.
export const gapStepText = (index: number | null, count: number): string =>
  index === null ? `${count} ${count === 1 ? 'gap' : 'gaps'}` : `Gap ${index} of ${count}`

export const gapChipLabel = (index: number | null, count: number): string => `Gaps ${index ?? '#'} /${count}`

// The first gap that starts after `time`, else the first of all (so the chip
// keeps cycling). The playhead sitting at a gap's start counts as being there.
export function nextGap(gaps: TranscribeGap[], time: number): TranscribeGap | null {
  if (gaps.length === 0) return null
  const sorted = [...gaps].sort((a, b) => a.start - b.start)
  return sorted.find((g) => g.start > time + 0.05) ?? sorted[0]
}

// The last gap that starts before `time`, else the last of all, so stepping back from the first wraps to the end.
export function prevGap(gaps: TranscribeGap[], time: number): TranscribeGap | null {
  if (gaps.length === 0) return null
  const sorted = [...gaps].sort((a, b) => a.start - b.start)
  return [...sorted].reverse().find((g) => g.start < time - 0.05) ?? sorted[sorted.length - 1]
}
