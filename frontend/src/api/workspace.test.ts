import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import {
  attachNovelEpub,
  attachNovelText,
  getDiarizationConfig,
  startDiarization,
  startTranscribe,
  updateTranscribeConfig,
  uploadAndTranscribe,
  uploadMedia,
} from './workspace'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

const file = new File(['abc'], 'a.mp3')

describe('workspace api', () => {
  it('uploads media as multipart without a Content-Type header', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await uploadMedia(3, file, false, fakeFetch(200, { name: 'source.mp3', size: 3, kind: 'audio' }, calls))
    expect(calls[0].url).toBe('/api/media/dramas/3/upload')
    expect(calls[0].init?.body).toBeInstanceOf(FormData)
    expect((calls[0].init!.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect((calls[0].init!.headers as Record<string, string>)['X-Baihe-Local']).toBe('1')
    expect((calls[0].init!.body as FormData).has('confirm_replace_audio')).toBe(false)
  })

  it('sends confirm_replace_audio only when replacing is confirmed', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const ok = fakeFetch(200, { upload: { name: 'x', size: 1, kind: 'audio' }, job_id: 'j' }, calls)
    await uploadMedia(3, file, true, ok)
    await uploadAndTranscribe(3, file, { run_diarize: false }, true, ok)
    for (const c of calls) expect((c.init!.body as FormData).get('confirm_replace_audio')).toBe('true')
  })

  it('sends upload-and-transcribe options as form fields, skipping unset ones', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await uploadAndTranscribe(
      1,
      file,
      { source_language: 'ja', run_diarize: true, expected_speakers: undefined, initial_prompt: '' },
      false,
      fakeFetch(200, { upload: { name: 'x', size: 1, kind: 'audio' }, job_id: 'j' }, calls),
    )
    const form = calls[0].init!.body as FormData
    expect(form.get('source_language')).toBe('ja')
    expect(form.get('run_diarize')).toBe('true')
    expect(form.has('expected_speakers')).toBe(false)
    expect(form.get('file')).toBeInstanceOf(File)
  })

  it('posts JSON bodies for run, config and novel text', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j', char_count: 1 }, calls)
    await startTranscribe(2, { source_language: 'zh', run_diarize: false }, f)
    await updateTranscribeConfig(2, { beam_size: 5 }, f)
    await attachNovelText(2, 'hello', 'append', f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/transcribe/dramas/2/run',
      '/api/transcribe/dramas/2/config',
      '/api/novel/dramas/2/attach-text',
    ])
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ source_language: 'zh', run_diarize: false })
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ text: 'hello', mode: 'append' })
  })

  it('carries tesseract_cmd in the run body when a caller sets it', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j' }, calls)
    await startTranscribe(2, { source_language: 'zh', tesseract_cmd: 'tesseract' }, f)
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ source_language: 'zh', tesseract_cmd: 'tesseract' })
  })

  it('passes expected speakers to diarization as a query and omits it when unset', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j' }, calls)
    await startDiarization(4, { expectedSpeakers: 0 }, f)
    await startDiarization(4, {}, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/diarization/dramas/4/run?expected_speakers=0',
      '/api/diarization/dramas/4/run',
    ])
  })

  it('sends overwrite_manual with confirm only when asked (D06)', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j' }, calls)
    await startDiarization(4, { expectedSpeakers: 3, overwriteManual: true }, f)
    await startDiarization(4, { overwriteManual: false }, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/diarization/dramas/4/run?expected_speakers=3&overwrite_manual=true&confirm=true',
      '/api/diarization/dramas/4/run',
    ])
  })

  it('reads the diarization config (D03)', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { drama_id: 4, expected_speakers: 3 }, calls)
    const c = await getDiarizationConfig(4, f)
    expect(c.expected_speakers).toBe(3)
    expect(calls[0].url).toBe('/api/diarization/dramas/4/config')
  })

  it('passes a speaker range as min_speakers/max_speakers', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j' }, calls)
    await startDiarization(4, { minSpeakers: 2, maxSpeakers: 4 }, f)
    await startDiarization(4, { maxSpeakers: 3 }, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/diarization/dramas/4/run?min_speakers=2&max_speakers=4',
      '/api/diarization/dramas/4/run?max_speakers=3',
    ])
  })

  it('sends the epub with its mode', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await attachNovelEpub(5, new File(['x'], 'b.epub'), 'replace', fakeFetch(200, { char_count: 1 }, calls))
    expect((calls[0].init!.body as FormData).get('mode')).toBe('replace')
  })

  it('surfaces API errors as ApiError', async () => {
    const f = fakeFetch(409, { error: { code: 'conflict', message: 'busy' } }, [])
    await expect(startTranscribe(1, {}, f)).rejects.toMatchObject({ status: 409, code: 'conflict' })
    await expect(startTranscribe(1, {}, f)).rejects.toBeInstanceOf(ApiError)
  })
})
