import { describe, expect, it } from 'vitest'

import { canRetranscribe, retranscribeDoneText } from './retranscribeLogic'

const done = (result: Record<string, unknown> | null, outcome: 'ok' | 'failed' | 'cancelled' | 'partial' = 'ok') => ({
  status: 'done',
  outcome,
  result,
})

describe('canRetranscribe', () => {
  it('needs an audio pipeline and audio on disk', () => {
    expect(canRetranscribe(null)).toBe(false)
    expect(canRetranscribe({ has_audio_pipeline: true, audio_available: true })).toBe(true)
    expect(canRetranscribe({ has_audio_pipeline: false, audio_available: true })).toBe(false)
    expect(canRetranscribe({ has_audio_pipeline: true, audio_available: false })).toBe(false)
  })
})

describe('retranscribeDoneText', () => {
  it('reports a replaced line', () => {
    expect(retranscribeDoneText(done({ line_count: 1 }))).toEqual({
      text: "Replaced this line's source text.", ok: true, changed: true,
    })
  })
  it('counts a CPU fallback (partial) as done', () => {
    expect(retranscribeDoneText(done({ line_count: 1, gpu_fallback: 'x' }, 'partial')).changed).toBe(true)
  })
  it('reports unchanged text', () => {
    const r = retranscribeDoneText(done({ line_count: 0 }))
    expect(r).toMatchObject({ ok: true, changed: false })
    expect(r.text).toMatch(/same text/)
  })
  it.each([
    ['empty', /No speech found/],
    ['line_changed', /your edit was kept/],
    ['line_gone', /merged, split or deleted/],
    ['model_download', /could not be downloaded/],
    ['audio_slice', /failed/],
  ])('explains failed_reason %s', (reason, text) => {
    const r = retranscribeDoneText(done({ failed_reason: reason }, 'failed'))
    expect(r.ok).toBe(false)
    expect(r.changed).toBe(false)
    expect(r.text).toMatch(text)
  })
  it('handles cancel and error status', () => {
    expect(retranscribeDoneText({ status: 'cancelled', outcome: 'cancelled', result: null }).text).toMatch(/Cancelled/)
    expect(retranscribeDoneText({ status: 'error', outcome: 'failed', result: null }).ok).toBe(false)
  })
})
