import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ApiError } from './client'
import {
  captionUrl,
  downloadRichDeck,
  pagePath,
  readerApi,
  readerEngines,
  vocabApkgUrl,
  wikiMarkdownUrl,
  TRANSLATION_ONLY,
} from './reader'
import type { TranslateEngine } from '../types/translate'

type Call = { url: string; init?: RequestInit }

function fakeFetch(status: number, body: unknown, calls: Call[], headers: Record<string, string> = {}) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    const payload = body instanceof Blob ? body : JSON.stringify(body)
    return new Response(payload, { status, headers })
  }) as typeof fetch
}

const engine = (name: string, free: boolean, key_configured = true): TranslateEngine => ({
  name,
  label: name,
  free,
  models: null,
  key_configured,
})

// No translation-only engine is offered now; the filter still applies to one.
beforeEach(() => TRANSLATION_ONLY.add('fake_mt'))
afterEach(() => TRANSLATION_ONLY.clear())

describe('reader api', () => {
  it('builds the page query, leaving max_width out when not given', () => {
    const base = { page: 2, chapter_size: 40, theme: 'dark' as const, font_size: 22, line_height: 2.4, font: 'serif' }
    expect(pagePath(3, base)).toBe(
      '/api/reader/dramas/3/page?page=2&chapter_size=40&theme=dark&font_size=22&line_height=2.4&font=serif',
    )
    expect(pagePath(3, { ...base, max_width: 1000 })).toContain('&max_width=1000')
  })

  it('posts progress, lookup and AI bodies to their routes', async () => {
    const calls: Call[] = []
    const f = fakeFetch(200, {}, calls)
    await readerApi.saveProgress(5, 3, 40, f)
    await readerApi.lookup(5, { page: 3, chapter_size: 40, use_llm: true, engine: 'ollama' }, f)
    await readerApi.who(5, { name: 'Xie Lian', engine: 'ollama', up_to_line_idx: 119 }, f)
    await readerApi.updateWiki(5, { engine: 'ollama', from_line_idx: 80 }, f)
    await readerApi.clearWiki(5, f)
    await readerApi.queueRich(5, ['你好'], false, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/reader/dramas/5/progress',
      '/api/reader/dramas/5/lookup',
      '/api/reader/dramas/5/story/who',
      '/api/reader/dramas/5/wiki/update',
      '/api/reader/dramas/5/wiki/clear',
      '/api/reader/dramas/5/vocab/rich',
    ])
    expect(calls.every((c) => c.init?.method === 'POST')).toBe(true)
    const bodies = calls.map((c) => JSON.parse(String(c.init?.body)))
    expect(bodies[0]).toEqual({ page: 3, chapter_size: 40 })
    expect(bodies[2]).toEqual({ name: 'Xie Lian', engine: 'ollama', up_to_line_idx: 119 })
    expect(bodies[4]).toEqual({ confirm: true })
    expect(bodies[5]).toEqual({ words: ['你好'], queued: false })
  })

  it('scopes the wiki list and its Markdown export', async () => {
    const calls: Call[] = []
    await readerApi.wiki(2, { up_to_line_idx: 39, entry_type: 'character' }, fakeFetch(200, {}, calls))
    await readerApi.wiki(2, {}, fakeFetch(200, {}, calls))
    expect(calls[0].url).toBe('/api/reader/dramas/2/wiki?up_to_line_idx=39&entry_type=character')
    expect(calls[1].url).toBe('/api/reader/dramas/2/wiki')
    expect(wikiMarkdownUrl(2, { up_to_line_idx: 0 })).toBe('/api/reader/dramas/2/wiki/export.md?up_to_line_idx=0')
    expect(captionUrl(2, 'English')).toBe('/api/reader/dramas/2/captions/English')
    expect(vocabApkgUrl(2)).toBe('/api/reader/dramas/2/vocab/export.apkg')
  })

  it('surfaces a 429 as an ApiError with the rate_limited code', async () => {
    const f = fakeFetch(429, { error: { code: 'rate_limited', message: 'busy' } }, [])
    await expect(readerApi.ask(1, { question: 'q', chat_history: [], engine: 'ollama' }, f)).rejects.toMatchObject({
      status: 429,
      code: 'rate_limited',
    })
  })

  it('downloads the sentence deck and reads the audio and cap headers', async () => {
    const calls: Call[] = []
    const f = fakeFetch(200, new Blob(['deck']), calls, {
      'Content-Disposition': 'attachment; filename="drama_4_vocab_sentence.apkg"',
      'X-Audio-Omitted': 'true',
      'X-Cards-Capped': '300',
    })
    const d = await downloadRichDeck(4, f)
    expect(calls[0].url).toBe('/api/reader/dramas/4/vocab/export.apkg?rich=true')
    expect(d.filename).toBe('drama_4_vocab_sentence.apkg')
    expect(d.audioOmitted).toBe(true)
    expect(d.cardsCapped).toBe(300)
    expect(await d.blob.text()).toBe('deck')

    const plain = await downloadRichDeck(4, fakeFetch(200, new Blob(['x']), []))
    expect(plain.audioOmitted).toBe(false)
    expect(plain.cardsCapped).toBeNull()
    expect(plain.filename).toBe('drama_4_vocab_sentence.apkg')
  })

  it('turns a failed deck download into an ApiError', async () => {
    const f = fakeFetch(429, { error: { code: 'rate_limited', message: 'busy' } }, [])
    const err = await downloadRichDeck(4, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(429)
    const offline = (async () => {
      throw new TypeError('down')
    }) as typeof fetch
    await expect(downloadRichDeck(4, offline)).rejects.toMatchObject({ code: 'network_error' })
  })

  it('offers only configured engines that can answer questions', () => {
    const all = [engine('ollama', true), engine('claude', false), engine('fake_mt', true), engine('gemini', false, false)]
    expect(readerEngines(all).map((e) => e.name)).toEqual(['ollama', 'claude'])
  })
})
