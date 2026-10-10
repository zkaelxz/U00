import { describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_FORM, DEFAULT_OPTIONS, FAST_CAPTIONS, checkOllama, LIVE_FORBIDDEN, LIVE_DEFAULT_MODEL, THINKING_SWITCH_ENGINES, pickEngine, advancedSummary, buildStartBody, hasPending, mergeCues, readFrom, checkLiveUrl, resolveModel,
  describeLiveError, feedCues, fmtTs, getLive, isActive, pickSession, startLive, statusLine, stopLive,
} from './live'
import { ApiError } from './client'

import type { LiveCue } from '../types/live'

const cue = (n: number, translation: LiveCue['translation'] = 'done'): LiveCue => ({
  id: n, start: n, end: n + 1, text: `t${n}`, translated: translation === 'done' ? `e${n}` : '', translation,
})
const SID = `live_${'a'.repeat(32)}`

describe('checkLiveUrl', () => {
  it('explains what is missing or wrong', () => {
    expect(checkLiveUrl('  ')).toMatch(/Still needed/)
    expect(checkLiveUrl('youtube.com/x')).toMatch(/https:/)
    expect(checkLiveUrl('ftp://x.test/a')).toMatch(/http/)
    expect(checkLiveUrl(`https://x.test/${'a'.repeat(2000)}`)).toMatch(/too long/)
    expect(checkLiveUrl(' https://www.youtube.com/watch?v=abc ')).toBeNull()
  })
})

describe('buildStartBody', () => {
  it('trims the link and sends defaults', () => {
    expect(buildStartBody({ ...DEFAULT_FORM, url: ' https://a.test/live ', engine: 'deepseek' })).toEqual({
      url: 'https://a.test/live', source_language: 'zh', whisper_size: 'small', segment_seconds: 20,
      overlap_seconds: 3, engine: 'deepseek', model: null, max_minutes: 60, use_gpu: false, reply_without_thinking: true,
    })
  })

  it('clamps numbers, caps overlap at half the chunk and drops unknown choices', () => {
    const b = buildStartBody({
      ...DEFAULT_FORM, url: 'https://a.test', segment_seconds: 2, overlap_seconds: 8, max_minutes: 9999,
      source_language: 'xx', whisper_size: 'huge', engine: '', use_gpu: true,
    })
    expect(b).toMatchObject({ segment_seconds: 3, overlap_seconds: 1.5, max_minutes: 240, source_language: 'zh', whisper_size: 'small', engine: null, use_gpu: true })
  })

  it('Fast captions is a short chunk that still goes out as chosen, and DEFAULT_FORM itself is unchanged', () => {
    expect(DEFAULT_FORM).toMatchObject({ segment_seconds: 20, overlap_seconds: 3, whisper_size: 'small', reply_without_thinking: true })
    const b = buildStartBody({ ...DEFAULT_FORM, ...FAST_CAPTIONS, url: 'https://a.test' })
    expect(b).toMatchObject({ segment_seconds: 4, overlap_seconds: 1, whisper_size: 'small', reply_without_thinking: true })
  })

  it('falls back to defaults for blank (NaN or null) numbers', () => {
    const b = buildStartBody({ ...DEFAULT_FORM, url: 'https://a.test', segment_seconds: NaN, overlap_seconds: null as unknown as number, max_minutes: NaN })
    expect(b).toMatchObject({ segment_seconds: 20, overlap_seconds: 3, max_minutes: 60 })
  })
})

describe('cues', () => {
  it('merges by id and keeps only the newest', () => {
    const prev = [cue(1), cue(2)]
    expect(mergeCues(prev, [])).toBe(prev)
    expect(mergeCues(prev, [cue(3), cue(4)], 3).map((c) => c.id)).toEqual([2, 3, 4])
  })

  it('fills a transcript in place when its translation arrives', () => {
    const merged = mergeCues([cue(1), cue(2, 'pending')], [cue(2), cue(3, 'pending')])
    expect(merged.map((c) => [c.id, c.translation])).toEqual([[1, 'done'], [2, 'done'], [3, 'pending']])
    expect(merged[1].translated).toBe('e2')
  })

  it('never puts a finished line back to pending from an older reply', () => {
    const merged = mergeCues([cue(1)], [cue(1, 'pending')])
    expect(merged[0].translation).toBe('done')
  })

  it('reads again from the oldest line still translating', () => {
    expect(readFrom([cue(0), cue(1, 'pending'), cue(2, 'pending')], 3)).toBe(1)
    expect(readFrom([cue(0), cue(1)], 2)).toBe(2)
    expect(hasPending([cue(0), cue(1, 'pending')])).toBe(true)
    expect(hasPending([cue(0)])).toBe(false)
  })

  it('shows the newest first', () => {
    expect(feedCues([cue(1), cue(2), cue(3)], 2).map((c) => c.start)).toEqual([3, 2])
  })

  it('formats timestamps', () => {
    expect(fmtTs(0)).toBe('0:00')
    expect(fmtTs(65.9)).toBe('1:05')
    expect(fmtTs(3725)).toBe('1:02:05')
    expect(fmtTs(NaN)).toBe('0:00')
  })
})

describe('sessions', () => {
  it('picks an active session, else the newest', () => {
    const s = (id: string, status: string) => ({ session_id: id, status, engine: null, cue_count: 0 })
    expect(pickSession([])).toBeNull()
    expect(pickSession([s('a', 'done'), s('b', 'running'), s('c', 'cancelled')])).toBe('b')
    expect(pickSession([s('a', 'done'), s('c', 'error')])).toBe('c')
    expect(isActive('queued')).toBe(true)
    expect(isActive('done')).toBe(false)
  })

  it('writes one status line', () => {
    expect(statusLine({ status: 'running', message: 'Chunk 3' }, 1)).toBe('Chunk 3 · 1 line')
    expect(statusLine({ status: 'queued', message: '' }, 0)).toMatch(/GPU/)
    expect(statusLine({ status: 'cancelled', message: 'Cancelled.' }, 4)).toBe('Stopped · 4 lines')
    expect(statusLine({ status: 'error', message: 'ffmpeg failed' }, 0)).toBe('Stopped with an error: ffmpeg failed')
  })

  it('shows the stage a chunk is in, and what a stop is waiting on', () => {
    expect(statusLine({ status: 'running', message: 'Capturing audio: waiting for chunk 2 (10 s of stream each)' }, 0))
      .toBe('Capturing audio: waiting for chunk 2 (10 s of stream each) · 0 lines')
    expect(statusLine({ status: 'running', message: 'Chunk 1: transcribing with Whisper small (GPU)' }, 1))
      .toBe('Chunk 1: transcribing with Whisper small (GPU) · 1 line')
    const slow = 'Chunk 1: translating with qwen3:8b (Ollama) Still waiting on Ollama after 75 s: it may be loading the model.'
    expect(statusLine({ status: 'running', message: slow }, 1)).toBe(`${slow} · 1 line`)
    const cancelling = 'Cancelling... Whisper is still transcribing chunk 1 and cannot be interrupted mid-chunk; it stops when that finishes (the previous chunk took about 12 s).'
    expect(statusLine({ status: 'running', message: cancelling }, 1)).toBe(`${cancelling} · 1 line`)
  })

  it('summarises the advanced options', () => {
    expect(advancedSummary({ ...DEFAULT_FORM })).toBe('Whisper small · chunk 20s · overlap 3s · stop after 60 min · CPU · no thinking')
    expect(DEFAULT_OPTIONS).not.toHaveProperty('url')
  })
})

describe('errors', () => {
  it('explains a refused start', () => {
    expect(describeLiveError(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe(LIVE_FORBIDDEN)
    expect(describeLiveError(new ApiError(409, { code: 'conflict', message: 'x' }))).toMatch(/already running/)
    expect(describeLiveError(new ApiError(503, { code: 'dependency_unavailable', message: 'No deepseek key is configured.' })))
      .toMatch(/No deepseek key/)
  })
})

describe('requests', () => {
  const ok = (body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }))

  it('posts the start body as JSON with the local header', async () => {
    const f = ok({ session_id: SID })
    await startLive(buildStartBody({ ...DEFAULT_FORM, url: 'https://a.test' }), f)
    const [path, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/live/sessions')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body)).url).toBe('https://a.test')
    expect((init.headers as Record<string, string>)['X-Baihe-Local']).toBe('1')
  })

  it('asks the server about its own default model when none is chosen', async () => {
    const f = ok({ ok: true, model: 'm', message: null })
    await checkOllama('', f)
    await checkOllama('qwen3:8b', f)
    expect(f.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      '/api/live/ollama-check', '/api/live/ollama-check?model=qwen3%3A8b',
    ])
  })

  it('polls with after and stops by id; a malformed id never reaches the network', async () => {
    const f = ok({})
    await getLive(SID, 7.9, f)
    await stopLive(SID, f)
    expect(f.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      `/api/live/sessions/${SID}?after=7`, `/api/live/sessions/${SID}/stop`,
    ])
    await expect(getLive('../x', 0, f)).rejects.toMatchObject({ status: 404 })
    expect(f).toHaveBeenCalledTimes(2)
  })
})

describe('default engine and model', () => {
  const engines = [{ name: 'deepseek' }, { name: 'ollama' }, { name: 'claude' }]
  it('starts on Ollama with gemma4:12b when nothing was chosen', () => {
    expect(pickEngine(engines, '')).toBe('ollama')
    expect(resolveModel({ name: 'ollama', models: ['qwen3:8b', LIVE_DEFAULT_MODEL] }, '')).toEqual({ model: LIVE_DEFAULT_MODEL, fellBack: false })
  })
  it('keeps a remembered engine, and falls to the first when Ollama is not listed', () => {
    expect(pickEngine(engines, 'deepseek')).toBe('deepseek')
    expect(pickEngine([{ name: 'claude' }, { name: 'deepseek' }], '')).toBe('claude')
    expect(pickEngine([], '')).toBe('')
  })
  it('does not name a default model Ollama does not offer', () => {
    expect(resolveModel({ name: 'ollama', models: ['qwen3:8b'] }, '')).toEqual({ model: '', fellBack: false })
  })
  it('knows which engines can switch thinking off', () => {
    expect(THINKING_SWITCH_ENGINES).toEqual(['deepseek', 'ollama'])
    expect(buildStartBody({ ...DEFAULT_FORM, url: 'https://a.test', reply_without_thinking: false }).reply_without_thinking).toBe(false)
  })
})

describe('model choice', () => {
  const ollama = { name: 'ollama', models: ['qwen3:8b', 'gemma4:12b'] }
  it('keeps an offered model and sends it', () => {
    expect(resolveModel(ollama, 'gemma4:12b')).toEqual({ model: 'gemma4:12b', fellBack: false })
    expect(buildStartBody({ ...DEFAULT_FORM, url: 'https://a.test', engine: 'ollama', model: 'gemma4:12b' }).model).toBe('gemma4:12b')
  })
  it('defaults to the engine default (null) and sends no model', () => {
    expect(resolveModel({ name: 'deepseek', models: ['deepseek-flash'] }, '')).toEqual({ model: '', fellBack: false })
    expect(buildStartBody({ ...DEFAULT_FORM, url: 'https://a.test' }).model).toBeNull()
  })
  it('drops a remembered model the engine no longer offers, or one with no list', () => {
    expect(resolveModel(ollama, 'gone:1b')).toEqual({ model: LIVE_DEFAULT_MODEL, fellBack: true })
    expect(resolveModel({ name: 'deepseek', models: null }, 'qwen3:8b')).toEqual({ model: '', fellBack: true })
    expect(resolveModel(undefined, 'qwen3:8b')).toEqual({ model: '', fellBack: true })
  })
  it('shows the model in the status line', () => {
    expect(statusLine({ status: 'running', message: 'Listening', model: 'qwen3:8b' }, 1)).toBe('Listening · 1 line · qwen3:8b')
  })
})
