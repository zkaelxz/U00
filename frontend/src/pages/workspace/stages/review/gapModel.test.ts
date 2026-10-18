import { describe, expect, it } from 'vitest'

import type { TranscribeGap } from '../../../../types/workspace'
import { gapChipLabel, GAP_PLAIN_TEXT, GAP_SPEECH_TEXT, gapText, nextGap, prevGap } from './gapModel'

const gap = (start: number, end: number, speech: boolean | null = null): TranscribeGap => ({
  start, end, seconds: end - start, pieces: 1, part: 1, parts: 1, after_line_id: null, before_line_id: null, speech,
})

describe('gap model', () => {
  it('says speech was detected only when the coverage check heard it', () => {
    expect(gapText(gap(0, 5, true))).toBe(GAP_SPEECH_TEXT)
    expect(gapText(gap(0, 5, true))).toBe('No line here: speech detected')
    expect(gapText(gap(0, 5, null))).toBe(GAP_PLAIN_TEXT)
  })

  it('labels the chip with the gap you are on and the count', () => {
    expect(gapChipLabel(2, 3)).toBe('Gaps 2 /3')
    expect(gapChipLabel(null, 3)).toBe('Gaps # /3')
  })

  it('jumps to the first gap after the playhead, in time order', () => {
    const gaps = [gap(200, 210), gap(10, 20), gap(100, 130)]
    expect(nextGap(gaps, 0)?.start).toBe(10)
    expect(nextGap(gaps, 15)?.start).toBe(100)
    expect(nextGap(gaps, 100)?.start).toBe(200)
  })

  it('wraps to the first gap after the last one, and has nothing to jump to without gaps', () => {
    expect(nextGap([gap(10, 20), gap(100, 130)], 150)?.start).toBe(10)
    expect(nextGap([], 5)).toBeNull()
  })

  it('steps back to the last gap that starts before the playhead, wrapping to the last gap', () => {
    const gaps = [gap(200, 210), gap(10, 20), gap(100, 130)]
    expect(prevGap(gaps, 150)?.start).toBe(100)
    expect(prevGap(gaps, 100)?.start).toBe(10)
    expect(prevGap(gaps, 5)?.start).toBe(200)
    expect(prevGap([], 5)).toBeNull()
  })
})
