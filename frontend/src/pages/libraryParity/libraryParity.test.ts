import { describe, expect, it } from 'vitest'

import {
  cacheHitShare, compactCount, costLabel, costMeta, countsLine, sharedLine, sharedSeries, usageLine,
} from './libraryParity'

const usage = { input_tokens: 812000, output_tokens: 301000, cache_read_tokens: 243600, estimated_cost_usd: 3.47, call_count: 318 }

describe('dashboard numbers (parity L01)', () => {
  it('cache-hit share is cache reads over input tokens, 0 with none logged', () => {
    expect(cacheHitShare(usage)).toBeCloseTo(0.3)
    expect(cacheHitShare({ input_tokens: 0, cache_read_tokens: 5 })).toBe(0)
  })
  it('usage line: API calls, and the cache share only once tokens are logged', () => {
    expect(usageLine(usage)).toBe('318 API calls · 30% cache hits')
    expect(usageLine({ ...usage, input_tokens: 0, call_count: 1 })).toBe('1 API call')
  })
  it('counts by status and type use the humanized labels', () => {
    expect(countsLine({ transcribed: 2, translated: 1 }, 'status')).toBe('Transcribed 2 · Translated 1')
    expect(countsLine({ audio_drama: 3 }, 'mediaType')).toBe('Audio drama 3')
    expect(countsLine({}, 'status')).toBe('')
  })
})

describe('cost rows (parity L04)', () => {
  it('free engines say so', () => {
    expect(costLabel(0)).toBe('$0.00 (free)')
    expect(costLabel(3.471)).toBe('$3.47')
  })
  it('compact token counts', () => {
    expect(compactCount(950)).toBe('950')
    expect(compactCount(1500)).toBe('1.5k')
    expect(compactCount(540000)).toBe('540k')
    expect(compactCount(1_250_000)).toBe('1.3M')
    expect(compactCount(2_000_000)).toBe('2M')
  })
  it('meta line: tokens in/out, cache hits, calls', () => {
    expect(costMeta({ input_tokens: 540000, output_tokens: 201000, cache_read_tokens: 162000, call_count: 212 }))
      .toBe('540k in · 201k out · 30% cache hits · 212 calls')
    expect(costMeta({ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, call_count: 1 }))
      .toBe('0 in · 0 out · 0% cache hits · 1 call')
  })
})

describe('series view (parity L05)', () => {
  const d = (media_type: string | null) => ({ media_type })
  it('keeps only series with 2+ dramas and counts their types', () => {
    const out = sharedSeries([
      { id: 1, dramas: [d('audio_drama'), d(null), d('novel')] },
      { id: 2, dramas: [d('manhua')] },
      { id: 3, dramas: [] },
    ])
    expect(out.map((s) => s.id)).toEqual([1])
    expect(out[0].types).toEqual({ audio_drama: 2, novel: 1 })
  })
  it('shared line', () => {
    expect(sharedLine({ character_count: 14, glossary_term_count: 1 })).toBe('14 shared characters · 1 glossary term')
  })
})
