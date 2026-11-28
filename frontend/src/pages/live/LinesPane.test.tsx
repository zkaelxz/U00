import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { LiveCue } from '../../types/live'
import { LinesPane } from './LinesPane'
import { newestEnglish, speakLine, spokenStep, waitsForATap } from './newestLine'

const cue = (id: number, translation = 'done', translated = `Line ${id}`): LiveCue =>
  ({ id, translation, translated, text: `第${id}句`, start: id * 5, end: id * 5 + 4 }) as LiveCue

describe('LinesPane for a screen reader', () => {
  it('announces only the newest English, never the list', () => {
    const html = renderToStaticMarkup(<LinesPane cues={[cue(3), cue(2), cue(1)]} total={3} lang="zh" waiting />)
    const region = /<p[^>]*aria-live="polite"[^>]*>([^<]*)<\/p>/.exec(html)
    expect(region?.[1]).toBe('Line 3')
    expect(html).toContain('role="log"')
    expect(html.match(/aria-live/g)).toHaveLength(1)
  })

  it('skips a newest line that is still translating or failed for the last one with English', () => {
    expect(newestEnglish([cue(4, 'pending', ''), cue(3, 'failed', 'oops'), cue(2), cue(1)])?.id).toBe(2)
    expect(newestEnglish([cue(1, 'skipped', '')])).toBeNull()
  })
})

describe('speakLine', () => {
  const synth = (pending: boolean) => ({ pending, speak: vi.fn(), cancel: vi.fn() })
  const utter = (t: string) => ({ text: t, lang: '' }) as unknown as SpeechSynthesisUtterance

  it('queues a line behind the one being read', () => {
    const s = synth(false)
    speakLine(s, 'Line 1', utter)
    expect(s.cancel).not.toHaveBeenCalled()
    expect(s.speak).toHaveBeenCalledWith(expect.objectContaining({ text: 'Line 1', lang: 'en-US' }))
  })

  it('drops the lines already waiting when it falls behind, so at most two are ever held', () => {
    const s = synth(true)
    speakLine(s, 'Line 9', utter)
    expect(s.cancel).toHaveBeenCalledOnce()
    expect(s.speak).toHaveBeenCalledOnce()
    expect(s.cancel.mock.invocationCallOrder[0]).toBeLessThan(s.speak.mock.invocationCallOrder[0])
  })
})

describe('spokenStep', () => {
  it('starts from the next line when turned on, says each line once, and forgets on off', () => {
    let step = spokenStep(null, cue(5), true)
    expect(step).toEqual({ said: 5, text: null, stop: false })
    step = spokenStep(step.said, cue(5), true)
    expect(step.text).toBeNull()
    step = spokenStep(step.said, cue(6), true)
    expect(step).toEqual({ said: 6, text: 'Line 6', stop: false })
    expect(spokenStep(step.said, cue(6), true).text).toBeNull()
    expect(spokenStep(step.said, cue(7), false)).toEqual({ said: null, text: null, stop: true })
    expect(spokenStep(null, null, true)).toEqual({ said: -1, text: null, stop: false })
  })

  it('stops the voice once when turned off, and leaves it alone while it stays off', () => {
    const on = spokenStep(null, cue(1), true)
    const off = spokenStep(on.said, cue(1), false)
    expect(off.stop).toBe(true)
    expect(spokenStep(off.said, cue(2), false).stop).toBe(false)
    expect(spokenStep(null, cue(3), false).stop).toBe(false)
    expect(spokenStep(spokenStep(on.said, cue(2), true).said, cue(3), true).stop).toBe(false)
  })

  it('knows when the browser still waits for a click before it may speak', () => {
    expect(waitsForATap({ userActivation: { hasBeenActive: false } })).toBe(true)
    expect(waitsForATap({ userActivation: { hasBeenActive: true } })).toBe(false)
    expect(waitsForATap({})).toBe(false)
  })
})
