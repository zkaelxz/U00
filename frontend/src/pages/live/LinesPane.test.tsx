import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { LiveCue } from '../../types/live'
import { LinesPane } from './LinesPane'
import { newestEnglish, speakLine } from './newestLine'

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
