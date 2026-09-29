import { describe, expect, it } from 'vitest'

import { canRetranscribe, jobIsForLine, retranscribeOutcome } from './retranscribeLogic'

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

describe('jobIsForLine', () => {
  it('matches by line id, and a record without one counts as ours', () => {
    expect(jobIsForLine({ result: { line_id: 7 } }, 7)).toBe(true)
    expect(jobIsForLine({ result: { line_id: 8 } }, 7)).toBe(false)
    expect(jobIsForLine({ result: null }, 7)).toBe(true)
    expect(jobIsForLine(null, 7)).toBe(true)
  })
})

describe('retranscribeOutcome', () => {
  it('a finished run is ready to fetch', () => {
    expect(retranscribeOutcome(done({ line_id: 1 }))).toEqual({ kind: 'ready' })
  })
  it('a CPU fallback (partial) is still ready', () => {
    expect(retranscribeOutcome(done({ line_id: 1, gpu_fallback: 'x' }, 'partial'))).toEqual({ kind: 'ready' })
  })
  it.each([
    ['empty', /No speech found/],
    ['line_gone', /merged, split or deleted/],
    ['model_download', /could not be downloaded/],
    ['audio_slice', /could not be cut/],
    ['something_else', /failed/],
  ])('explains failed_reason %s', (reason, text) => {
    const r = retranscribeOutcome(done({ line_id: 1, failed_reason: reason }, 'failed'))
    expect(r.kind).toBe('none')
    expect(r.kind === 'none' && r.text).toMatch(text)
  })
  it('a cancelled or errored run is not ready', () => {
    expect(retranscribeOutcome({ status: 'cancelled', outcome: 'cancelled', result: null })).toEqual({
      kind: 'none', text: 'Cancelled. The line was not changed.',
    })
    expect(retranscribeOutcome({ status: 'error', outcome: 'failed', result: null }).kind).toBe('none')
  })
})
