import { describe, expect, it } from 'vitest'

import { summarizeEngineFailure } from './errorMessages'


describe('summarizeEngineFailure', () => {
  const ollamaRefused =
    "HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded ... [WinError 10061] No connection could be made"
  it('says Ollama is not running and points to ollama.com', () => {
    const s = summarizeEngineFailure('ollama', ollamaRefused, 'Ollama')
    expect(s.kind).toBe('not_running')
    expect(s.summary).toMatch(/Ollama isn't running.*ollama\.com.*pull a model/)
  })
  it('detects a missing Ollama model', () => {
    expect(summarizeEngineFailure('ollama', "model 'x' not found, try pulling it first (404)").kind).toBe('no_model')
  })
  it('classifies keys, rate limits and timeouts', () => {
    expect(summarizeEngineFailure('claude', 'HTTPError: 401 Unauthorized', 'Claude').summary).toBe(
      'Claude rejected the key. Check its key under Engines and keys, then test again.',
    )
    expect(summarizeEngineFailure('gemini', '403 Forbidden').kind).toBe('key_rejected')
    expect(summarizeEngineFailure('gemini', '429 quota exceeded').kind).toBe('rate_limited')
    expect(summarizeEngineFailure('claude', 'No answer within 45 seconds.').kind).toBe('timed_out')
  })
  it('treats a refused cloud connection as unreachable and unknown text as generic', () => {
    expect(summarizeEngineFailure('claude', 'Max retries exceeded').kind).toBe('unreachable')
    expect(summarizeEngineFailure('claude', 'weird', 'Claude')).toEqual({ kind: 'other', summary: 'The Claude test failed.' })
    expect(summarizeEngineFailure('claude', null).kind).toBe('other')
  })
})
