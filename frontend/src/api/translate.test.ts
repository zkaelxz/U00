import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import {
  engineNotesHelp,
  engineOptionLabel,
  engineShortName,
  engineSummary,
  languagePair,
  modelOptionLabel,
  translateApi,
  usableEngines,
  MAX_TRANSLATE_TEXT_CHARS,
  validateTranslateInput,
} from './translate'

describe('engine display helpers', () => {
  it('uses a short name, never the long description', () => {
    expect(engineShortName({ name: 'deepseek' })).toBe('DeepSeek')
    expect(engineShortName({ name: 'some_new_engine' })).toBe('Some new engine')
  })

  it('shows the short name in the closed select and the notes in the help', () => {
    expect(engineOptionLabel({ name: 'deepseek', key_configured: true })).toBe('DeepSeek')
    expect(engineOptionLabel({ name: 'openai', key_configured: false })).toBe('OpenAI (no key)')
    expect(
      engineNotesHelp([
        { name: 'claude', label: 'Paid, cloud.' },
        { name: 'ollama', label: 'Free and private.' },
      ]),
    ).toBe('Claude: Paid, cloud. Ollama (local): Free and private.')
  })

  it('summarises a long label as its first sentence', () => {
    const long = 'Paid, cloud, very cheap -- roughly 5-10 cents per drama. Strong on Chinese.'
    expect(engineSummary(long)).toBe('Paid, cloud, very cheap')
    expect(engineSummary('Paid, cloud. Best for tone.')).toBe('Paid, cloud.')
    expect(engineSummary('Paid, cloud, very cheap; strong on Chinese.')).toBe(
      'Paid, cloud, very cheap; strong on Chinese.',
    )
    expect(engineSummary('x'.repeat(300)).length).toBeLessThanOrEqual(140)
  })
})

const engine = (name: string, key_configured: boolean) => ({
  name,
  label: name,
  free: false,
  models: null,
  key_configured,
})

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('translate logic', () => {
  it('filters engines without keys', () => {
    expect(usableEngines([engine('a', true), engine('b', false)]).map((e) => e.name)).toEqual(['a'])
  })
  it('builds the language pair per direction', () => {
    expect(languagePair('to_english', 'zh')).toEqual({ source_language: 'zh', target_language: 'en' })
    expect(languagePair('from_english', 'ko')).toEqual({ source_language: 'en', target_language: 'ko' })
  })
  it('validates input', () => {
    expect(validateTranslateInput('  ', 'x')).toMatch(/text/)
    expect(validateTranslateInput('hi', '')).toMatch(/engine/)
    expect(validateTranslateInput('hi', 'x')).toBeNull()
    expect(validateTranslateInput('a'.repeat(MAX_TRANSLATE_TEXT_CHARS), 'x')).toBeNull()
    expect(validateTranslateInput('a'.repeat(MAX_TRANSLATE_TEXT_CHARS + 1), 'x')).toMatch(/2,000,000 characters/)
  })
})

describe('translate api', () => {
  it('unwraps engines and history', async () => {
    const calls: { url: string }[] = []
    expect(await translateApi.engines(fakeFetch(200, { items: [engine('a', true)] }, calls))).toHaveLength(1)
    expect(await translateApi.history(10, fakeFetch(200, { items: [] }, calls))).toEqual([])
    expect(calls.map((c) => c.url)).toEqual(['/api/translate/engines', '/api/translate/history?limit=10'])
  })
  it('posts and returns the translated text', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const out = await translateApi.translate(
      { text: 'hi', engine: 'ollama', source_language: 'en', target_language: 'zh' },
      fakeFetch(200, { translated_text: 'yo' }, calls),
    )
    expect(out).toBe('yo')
    expect(calls[0].init?.method).toBe('POST')
  })
  it('clears history as a confirmed PC-only DELETE', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    expect(await translateApi.clearHistory(fakeFetch(200, { cleared: true }, calls))).toEqual({ cleared: true })
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe('/api/translate/history?confirm=true')
    expect(calls[0].init?.method).toBe('DELETE')
    expect(new Headers(calls[0].init?.headers).get('X-Baihe-Local')).toBe('1')
  })
  it('surfaces a 503 as ApiError', async () => {
    const f = fakeFetch(503, { error: { code: 'dependency_unavailable', message: 'No key.' } })
    const err = await translateApi
      .translate({ text: 'a', engine: 'claude', source_language: 'zh', target_language: 'en' }, f)
      .catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(503)
  })
})

describe('modelOptionLabel', () => {
  const engine = { model_labels: { 'claude-new': 'claude-new -- newly listed (cost estimated at highest Claude rate)' } }
  it('uses the server label for an offered extra model', () => {
    expect(modelOptionLabel(engine, 'claude-new')).toContain('newly listed')
  })
  it('labels any server-flagged cloud model, even without a server label', () => {
    const cloud = { cloud_models: ['gpt-oss:120b-cloud'] }
    expect(modelOptionLabel(cloud, 'gpt-oss:120b-cloud')).toMatch(/CLOUD: sends text off this PC/)
    expect(modelOptionLabel(cloud, 'gemma4:12b')).toBe('gemma4:12b')
  })
  it('falls back to the id', () => {
    expect(modelOptionLabel(engine, 'claude-sonnet-5')).toBe('claude-sonnet-5')
    expect(modelOptionLabel(undefined, 'x')).toBe('x')
  })
})
