import { describe, expect, it } from 'vitest'

import type { LiveCue } from '../../types/live'
import { toSrt, toText } from './sessionExport'

const cue = (id: number, start: number, end: number, translation: LiveCue['translation'] = 'done', translated = `en${id}`): LiveCue =>
  ({ id, start, end, text: `src${id}`, translated, translation })

describe('toSrt', () => {
  it('writes numbered blocks with comma milliseconds and the translation above the original', () => {
    const out = toSrt([cue(0, 1.5, 3), cue(1, 3725.25, 3727)], 'both')
    expect(out).toBe('1\n00:00:01,500 --> 00:00:03,000\nen0\nsrc0\n\n2\n01:02:05,250 --> 01:02:07,000\nen1\nsrc1\n')
  })

  it('falls back to the transcript for a line with no usable translation', () => {
    const cues = [cue(0, 0, 2, 'failed', '[translation failed]'), cue(1, 2, 4, 'pending', ''), cue(2, 4, 6, 'skipped', '')]
    expect(toSrt(cues, 'translation')).toContain('\nsrc0\n')
    expect(toSrt(cues, 'translation')).not.toContain('failed')
    expect(toSrt(cues, 'both')).not.toContain('en')
  })

  it('gives an empty or backwards cue half a second and numbers only lines with text', () => {
    const blank = { ...cue(1, 5, 6), text: ' ', translated: ' ' }
    const out = toSrt([cue(0, 4, 4), blank, cue(2, 8, 9)], 'original')
    expect(out).toBe('1\n00:00:04,000 --> 00:00:04,500\nsrc0\n\n2\n00:00:08,000 --> 00:00:09,000\nsrc2\n')
  })

  it('is empty when there is nothing to save', () => {
    expect(toSrt([], 'both')).toBe('')
  })
})

describe('toText', () => {
  it('heads each line with its time since the session started', () => {
    expect(toText([cue(0, 65, 67), cue(1, 3725, 3727)], 'translation')).toBe('[1:05] en0\n\n[1:02:05] en1\n')
  })
})
