import { describe, expect, it } from 'vitest'

import type { LiveCue } from '../../types/live'
import { HOLD_MAX_S, HOLD_MIN_S, captionAt } from './captions'

const cue = (id: number, start: number, translation: LiveCue['translation'] = 'done', end = start + 2): LiveCue => ({
  id, start, end, text: `src${id}`, translated: translation === 'done' ? `en${id}` : '', translation,
})
// Session timeline 0 is wall-clock 1_000_000 ms; every cue reached the browser 4 s after its speech.
const arrivals = (cues: LiveCue[]) => new Map(cues.map((c) => [c.id, 1_000_000 + c.start * 1000 + 4000]))

describe('captionAt', () => {
  it('shows nothing before any line is known', () => {
    expect(captionAt([], new Map(), 5, 0)).toBeNull()
  })

  it('waits for the delayed picture, not the arrival', () => {
    const cues = [cue(0, 10)]
    const seen = arrivals(cues)
    expect(captionAt(cues, seen, 8, 1_000_000 + 21_000)).toBeNull()
    expect(captionAt(cues, seen, 8, 1_000_000 + 22_500)?.text).toBe('en0')
  })

  it('is never due before the line arrived', () => {
    // The first line reached the browser 1 s after its speech, the second 6 s after.
    const cues = [cue(0, 0), cue(1, 10)]
    const seen = new Map([[0, 1_001_000], [1, 1_016_000]])
    expect(captionAt(cues, seen, 0, 1_015_000)).toBeNull()
    expect(captionAt(cues, seen, 0, 1_016_000)?.id).toBe(1)
  })

  it('draws the transcript until the translation is ready', () => {
    const now = 1_000_000 + 14_500
    expect(captionAt([cue(0, 10, 'pending')], arrivals([cue(0, 10)]), 0, now)).toEqual({ id: 0, text: 'src0', pending: true })
    expect(captionAt([cue(0, 10, 'failed')], arrivals([cue(0, 10)]), 0, now)).toMatchObject({ text: 'src0', pending: false })
    expect(captionAt([cue(0, 10, 'cancelled')], arrivals([cue(0, 10)]), 0, now)?.text).toBe('src0')
  })

  it('clears after a few seconds', () => {
    const cues = [cue(0, 10, 'done', 11)]
    const from = 1_000_000 + 14_000
    expect(captionAt(cues, arrivals(cues), 0, from + HOLD_MIN_S * 1000 - 1)).not.toBeNull()
    expect(captionAt(cues, arrivals(cues), 0, from + HOLD_MIN_S * 1000)).toBeNull()
    const long = [cue(0, 10, 'done', 60)]
    expect(captionAt(long, arrivals(long), 0, from + HOLD_MAX_S * 1000)).toBeNull()
  })

  it('replaces the line when the next one is due', () => {
    const cues = [cue(0, 10), cue(1, 12)]
    const seen = arrivals(cues)
    expect(captionAt(cues, seen, 0, 1_000_000 + 14_500)?.id).toBe(0)
    expect(captionAt(cues, seen, 0, 1_000_000 + 16_500)?.id).toBe(1)
  })
})
