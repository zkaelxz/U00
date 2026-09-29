import { describe, expect, it } from 'vitest'

import {
  buildDubRequest,
  dubBlocker,
  formatFactor,
  formatMs,
  initialDubForm,
  pacingRows,
  pacingSummary,
} from '../pages/workspace/stages/dubForm'
import type { DubConfig } from '../types/dub'
import { ApiError } from './client'
import { dubApi, dubTrackUrl, narrationApi } from './dub'

const cfg = (over: Partial<DubConfig> = {}): DubConfig => ({
  drama_id: 1,
  content_mode: null,
  is_narration: false,
  narration_language: 'en',
  narration_language_options: ['en', 'zh'],
  source_language: 'zh',
  tts_engines: [{ key: 'edge_tts', label: 'Edge', requires_internet: true }],
  defaults: { max_speedup: 1.3, max_slowdown: 0.85, speedup_range: [1, 2], slowdown_range: [0.5, 1] },
  gpu_required: false,
  speakable_line_count: 4,
  track_available: false,
  gpt_sovits_configured: false,
  can_keep_background: false,
  ...over,
})

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('dub request logic', () => {
  it('sends pacing limits for timed dubs, clamped, and never keep_background unless allowed', () => {
    const form = { ...initialDubForm(cfg()), maxSpeedup: 5, maxSlowdown: 0.1, keepBackground: true }
    expect(buildDubRequest(cfg(), form)).toEqual({
      tts_engine: 'edge_tts', max_speedup: 2, max_slowdown: 0.5, keep_background: false,
    })
    expect(buildDubRequest(cfg({ can_keep_background: true }), form).keep_background).toBe(true)
  })
  it('sends the language instead of pacing for narration', () => {
    const c = cfg({ is_narration: true, defaults: null })
    expect(buildDubRequest(c, initialDubForm(c))).toEqual({
      tts_engine: 'edge_tts', narration_language: 'en', keep_background: false,
    })
  })
  it('explains why a run is blocked', () => {
    expect(dubBlocker(cfg({ speakable_line_count: 0 }), initialDubForm(cfg()))).toMatch(/no text/i)
    expect(dubBlocker(cfg({ tts_engines: [] }), initialDubForm(cfg({ tts_engines: [] })))).toMatch(/engine/)
    expect(dubBlocker(cfg(), initialDubForm(cfg()))).toBeNull()
  })
  it('renders null pacing numbers safely and picks overflow rows', () => {
    expect(formatMs(null)).toBe('n/a')
    expect(formatMs(1234.6)).toBe('1235 ms')
    expect(formatFactor(null)).toBe('n/a')
    expect(pacingSummary({ fit: 2, overflow: 1 })).toBe('2 fit · 1 overflow')
    const lines = [
      { idx: 1, status: 'fit', factor: 1, clip_ms: null, window_ms: null },
      { idx: 2, status: 'overflow', factor: 1.5, clip_ms: 9, window_ms: 5 },
    ]
    expect(pacingRows(lines).map((l) => l.idx)).toEqual([2])
    expect(pacingRows([lines[0]]).map((l) => l.idx)).toEqual([1])
  })
})

describe('dub api', () => {
  it('posts the run body and returns the job id', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const r = await dubApi.run(7, { tts_engine: 'edge_tts', keep_background: false }, fakeFetch(200, { job_id: 'j' }, calls))
    expect(r.job_id).toBe('j')
    expect(calls[0].url).toContain('/api/dub/dramas/7/run')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ tts_engine: 'edge_tts', keep_background: false })
  })
  it('reads configs and surfaces 409/422/503 as ApiError', async () => {
    expect((await narrationApi.config(2, fakeFetch(200, { is_narration: true }))).is_narration).toBe(true)
    for (const status of [409, 422, 503]) {
      const err = await dubApi
        .run(1, { tts_engine: 'x', keep_background: false }, fakeFetch(status, { error: { code: 'conflict', message: 'm' } }))
        .catch((e: unknown) => e)
      expect(err).toBeInstanceOf(ApiError)
      expect((err as ApiError).status).toBe(status)
    }
  })
})

describe('dubTrackUrl', () => {
  it('points at the dub track route', () => {
    expect(dubTrackUrl(7)).toMatch(/\/api\/dub\/dramas\/7\/track$/)
  })
})
