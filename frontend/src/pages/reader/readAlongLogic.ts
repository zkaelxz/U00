// Pure helpers for the Reader's read-along list beside the narration player.
import type { ReviewLine } from '../../types/review'

export interface AlongLine {
  id: number
  start: number
  text: string
}

// The line list the narration was made from, in the language the voice speaks.
// Lines with nothing to show (silence markers, an untranslated English line) are left out.
export function alongLines(lines: ReviewLine[], language: string): AlongLine[] {
  return lines
    .map((l) => ({ id: l.id, start: l.start, text: (language === 'original' ? l.zh : l.en).trim() }))
    .filter((l) => l.text !== '')
    .sort((a, b) => a.start - b.start)
}

/** A line counts as started this long before its start time, so a seek to a line's start lights that line even when
 *  the media reports a time just short of it. Two lines whose starts are closer than this start together. */
export const START_SLACK_S = 0.05

// The last line that has started. The line stays lit through the pause after it, so the
// highlight never flickers off between two lines; null before the first one.
export function activeIndex<T extends Pick<AlongLine, 'start'>>(lines: readonly T[], time: number): number | null {
  let found: number | null = null
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].start <= time + START_SLACK_S) found = i
    else break
  }
  return found
}
