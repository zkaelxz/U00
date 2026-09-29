import { afterEach, describe, expect, it } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  candidateAudioUrl,
  chooseCandidate,
  extractCandidates,
  linkSeriesCharacter,
  listCandidates,
  removeReferenceClip,
  saveToVoiceBank,
  uploadReferenceClip,
} from './voiceClone'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[], status = 200) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(status === 200 ? '{}' : '{"error":{"code":"forbidden","message":"Not allowed."}}', { status })
  }) as typeof fetch
}

const header = (c: Call, name: string) => new Headers(c.init?.headers).get(name)

afterEach(() => resetPcModeForTests())

describe('voice-clone API', () => {
  it('uploads a clip as multipart with the speaker in the form and the local header', async () => {
    const calls: Call[] = []
    await uploadReferenceClip(4, 'SPEAKER 00/x', new Blob(['RIFF']), 'a.wav', ' hi ', fakeFetch(calls))
    const c = calls[0]
    expect(c.url).toBe('/api/characters/dramas/4/reference-clip')
    expect(c.init?.method).toBe('POST')
    expect(header(c, 'X-Baihe-Local')).toBe('1')
    expect(header(c, 'Content-Type')).toBeNull()
    const form = c.init?.body as FormData
    expect(form.get('speaker_label')).toBe('SPEAKER 00/x')
    expect(form.get('ref_text')).toBe('hi')
    expect((form.get('file') as File).name).toBe('a.wav')
  })

  it('omits a blank transcript', async () => {
    const calls: Call[] = []
    await uploadReferenceClip(4, 'A', new Blob(['x']), 'a.wav', '  ', fakeFetch(calls))
    const form = calls[0].init?.body as FormData
    expect(form.has('ref_text')).toBe(false)
  })

  it('removes with confirm and flips to remote on a 403', async () => {
    const calls: Call[] = []
    await expect(removeReferenceClip(4, 'A', fakeFetch(calls, 403))).rejects.toMatchObject({ status: 403 })
    expect(calls[0].url).toBe('/api/characters/dramas/4/reference-clip/remove')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'A', confirm: true })
    expect(getPcMode()).toBe('remote')
  })

  it('extract, list, choose, bank save and series link use body-keyed labels', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await extractCandidates(4, 'A', 2, f)
    await listCandidates(4, f)
    await chooseCandidate(4, 'ab'.repeat(16), f)
    await saveToVoiceBank(4, 'A', '  Mei  ', '', f)
    await linkSeriesCharacter(4, 'A', null, f)
    expect(calls.map((c) => `${c.init?.method ?? 'GET'} ${c.url}`)).toEqual([
      'POST /api/characters/dramas/4/reference-clips/extract',
      'GET /api/characters/dramas/4/reference-clips/candidates',
      `POST /api/characters/dramas/4/reference-clips/candidates/${'ab'.repeat(16)}/choose`,
      'POST /api/characters/dramas/4/voice-bank/save',
      'POST /api/characters/dramas/4/series-link',
    ])
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'A', max_candidates: 2 })
    expect(calls[2].init?.body).toBeUndefined()
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({ speaker_label: 'A', name: 'Mei', notes: '' })
    expect(JSON.parse(String(calls[4].init?.body))).toEqual({ speaker_label: 'A', series_character_id: null })
  })

  it('builds the candidate audio URL', () => {
    expect(candidateAudioUrl(4, 'ab'.repeat(16))).toBe(
      `/api/characters/dramas/4/reference-clips/candidates/${'ab'.repeat(16)}/audio`,
    )
  })
})
