import { describe, expect, it } from 'vitest'

import { asrBackendOptions, batchingNote, deviceNote, downloadProblem, getAsrOptions, getDiarizationConfig, parseBatchSize, startVoiceDetectorDownload, updateAsrOptions, voiceDetectorNote } from './asrOptions'

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
  qwen_asr_version: '0.0.6',
  qwen_asr_batching_available: true,
  qwen_vad_refine_timing: false,
  mixed_languages: false,
  voice_detector: 'auto' as const,
  asmr_vad_onnxruntime_installed: true,
  asmr_vad_model_downloaded: false,
  asmr_vad_download_job_id: 'asmr_vad_download',
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

  it('saves the mixed languages switch on its own', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const saved = await updateAsrOptions({ mixed_languages: true }, fakeFetch(200, { ...OPTS, mixed_languages: true }, calls))
    expect(saved.mixed_languages).toBe(true)
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ mixed_languages: true })
  })

  it('reads the diarization config for the device note', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const cfg = { drama_id: 3, hf_token_configured: true, expected_speakers: null, min_speakers: null, max_speakers: null, last_device: 'cuda', audio_available: true }
    expect((await getDiarizationConfig(3, fakeFetch(200, cfg, calls))).last_device).toBe('cuda')
    expect(calls[0].url).toContain('/api/diarization/dramas/3/config')
  })
})

describe('asrBackendOptions', () => {
  it('lists the selectable backends', () => {
    expect(asrBackendOptions()).toEqual(['whisper', 'qwen3_asr', 'qwen3_asr_vad', 'qwen3_asr_long'])
  })
})

describe('batchingNote (Step 103)', () => {
  it('says when batching can and cannot run', () => {
    expect(batchingNote({ qwen_asr_version: '0.0.6', qwen_asr_batching_available: true })).toMatch(/can run/)
    expect(batchingNote({ qwen_asr_version: '0.0.9', qwen_asr_batching_available: false })).toMatch(/one at a time/)
    expect(batchingNote({ qwen_asr_version: null, qwen_asr_batching_available: false })).toMatch(/not installed/)
  })
})

describe('voice detector', () => {
  it('says nothing for Standard, and names what the ASMR detector still needs', () => {
    expect(voiceDetectorNote({ ...OPTS, voice_detector: 'standard' })).toBeNull()
    expect(voiceDetectorNote({ ...OPTS, asmr_vad_onnxruntime_installed: false })).toMatch(/onnxruntime/)
    expect(voiceDetectorNote(OPTS)).toMatch(/not downloaded/)
    expect(voiceDetectorNote({ ...OPTS, asmr_vad_model_downloaded: true })).toBeNull()
  })

  it('saves the choice and starts the download with POSTs', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await updateAsrOptions({ voice_detector: 'asmr' }, fakeFetch(200, { ...OPTS, voice_detector: 'asmr' }, calls))
    await startVoiceDetectorDownload(fakeFetch(200, { job_id: 'asmr_vad_download', started: true }, calls))
    expect(calls[0].url).toBe('/api/settings/asr-options')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ voice_detector: 'asmr' })
    expect(calls[1].url).toBe('/api/settings/asr-options/voice-detector/download')
    expect(calls[1].init?.method).toBe('POST')
  })
})

describe('downloadProblem', () => {
  const base = { error: null, outcome_message: null, result: null }
  it('is null while the job runs or after it worked', () => {
    expect(downloadProblem({ ...base, status: 'running' })).toBeNull()
    expect(downloadProblem({ ...base, status: 'done' })).toBeNull()
  })
  it('names a cancelled download', () => {
    expect(downloadProblem({ ...base, status: 'cancelled' })).toMatch(/cancelled/)
  })
  it('prefers the failure detail the job recorded', () => {
    expect(downloadProblem({ ...base, status: 'error', error: 'x', result: { detail: 'Could not reach the model download.' } }))
      .toBe('Could not reach the model download.')
    expect(downloadProblem({ ...base, status: 'error', error: 'Disk full.' })).toBe('Disk full.')
    expect(downloadProblem({ ...base, status: 'error' })).toMatch(/failed/)
  })
})
