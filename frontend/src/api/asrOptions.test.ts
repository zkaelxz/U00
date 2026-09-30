import { describe, expect, it } from 'vitest'

import { asrBackendOptions, deviceNote, getAsrOptions, getDiarizationConfig, parseBatchSize, updateAsrOptions } from './asrOptions'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

const OPTS = {
  qwen_asr_batch_size: 1,
  qwen_asr_batch_min: 1,
  qwen_asr_batch_max: 16,
  moss_experimental: false,
  moss_installed: false,
}

describe('deviceNote (Step 101)', () => {
  it('names the GPU or CPU in plain words, and nothing before a run', () => {
    expect(deviceNote('cuda')).toMatch(/GPU/)
    expect(deviceNote('cpu')).toMatch(/CPU/)
    expect(deviceNote(null)).toBeNull()
    expect(deviceNote(undefined)).toBeNull()
    expect(deviceNote('')).toBeNull()
  })
})

describe('parseBatchSize (Step 103)', () => {
  it('accepts whole numbers within range only', () => {
    expect(parseBatchSize('4', 1, 16)).toBe(4)
    expect(parseBatchSize(' 16 ', 1, 16)).toBe(16)
    expect(parseBatchSize('1', 1, 16)).toBe(1)
    for (const bad of ['', '0', '17', '2.5', '-1', 'abc']) expect(parseBatchSize(bad, 1, 16)).toBeNull()
  })
})

describe('asr options API', () => {
  it('reads and saves the settings at /api/settings/asr-options', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    expect(await getAsrOptions(fakeFetch(200, OPTS, calls))).toEqual(OPTS)
    await updateAsrOptions({ qwen_asr_batch_size: 4 }, fakeFetch(200, { ...OPTS, qwen_asr_batch_size: 4 }, calls))
    expect(calls[0].url).toContain('/api/settings/asr-options')
    expect(calls[1].url).toContain('/api/settings/asr-options')
    expect(calls[1].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ qwen_asr_batch_size: 4 })
  })

  it('reads the diarization config for the device note', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const cfg = { drama_id: 3, hf_token_configured: true, expected_speakers: null, min_speakers: null, max_speakers: null, last_device: 'cuda', audio_available: true }
    expect((await getDiarizationConfig(3, fakeFetch(200, cfg, calls))).last_device).toBe('cuda')
    expect(calls[0].url).toContain('/api/diarization/dramas/3/config')
  })
})

describe('asrBackendOptions (Step 104)', () => {
  it('offers MOSS only while the experimental toggle is on', () => {
    expect(asrBackendOptions(false)).toEqual(['whisper', 'qwen3_asr'])
    expect(asrBackendOptions(true)).toEqual(['whisper', 'qwen3_asr', 'moss_td'])
  })
})
