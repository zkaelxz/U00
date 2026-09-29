import { describe, expect, it } from 'vitest'

import { canRetranscribe, retranscribeOutcome } from './retranscribeLogic'

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

describe('retranscribeOutcome', () => {
  it('offers the proposal with the text it started from', () => {
    expect(retranscribeOutcome(done({ proposed_zh: '新的', base_zh: '旧的' }))).toEqual({
      kind: 'proposal', proposed: '新的', base: '旧的', same: false,
    })
  })
  it('a CPU fallback (partial) still offers the proposal', () => {
    expect(retranscribeOutcome(done({ proposed_zh: '新', base_zh: '', gpu_fallback: 'x' }, 'partial')).kind).toBe('proposal')
  })
  it('flags a proposal equal to the current text', () => {
    expect(retranscribeOutcome(done({ proposed_zh: '同', base_zh: '同' }))).toMatchObject({ same: true })
  })
  it.each([
    ['empty', /No speech found/],
    ['line_gone', /merged, split or deleted/],
    ['model_download', /could not be downloaded/],
    ['audio_slice', /failed/],
  ])('explains failed_reason %s', (reason, text) => {
    const r = retranscribeOutcome(done({ failed_reason: reason }, 'failed'))
    expect(r.kind).toBe('none')
    expect(r.kind === 'none' && r.text).toMatch(text)
  })
  it('never offers a proposal from a failed, cancelled or result-less job', () => {
    expect(retranscribeOutcome({ status: 'cancelled', outcome: 'cancelled', result: null })).toEqual({
      kind: 'none', text: 'Cancelled. The line was not changed.',
    })
    expect(retranscribeOutcome({ status: 'error', outcome: 'failed', result: { proposed_zh: 'x' } }).kind).toBe('none')
    expect(retranscribeOutcome(done(null)).kind).toBe('none')
  })
})
