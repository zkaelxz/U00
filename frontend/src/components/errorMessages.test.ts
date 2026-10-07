import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { describeError, summarizeEngineFailure } from './errorMessages'


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

describe('describeError for Ollama failures', () => {
  it('shows the server sentence as the title instead of the generic text', () => {
    const msg = "Ollama isn't running. Start it, or pick another translator in Settings."
    const err = new ApiError(503, { code: 'dependency_unavailable', message: msg, details: { reason: 'ollama_unreachable' } })
    expect(describeError(err)).toEqual({ title: msg, detail: null })
  })
  it('keeps the generic text for other dependency errors', () => {
    const err = new ApiError(503, { code: 'dependency_unavailable', message: 'No claude key is configured.' })
    expect(describeError(err).title).toMatch(/not installed or not reachable/)
  })
})

describe('describeError for a missing key', () => {
  const noKey = (engine: unknown) =>
    new ApiError(503, {
      code: 'dependency_unavailable',
      message: 'No claude key is configured. Set one in Settings first.',
      details: { reason: 'no_key', engine },
    })
  it('has its own heading, not the missing-package one', () => {
    const { title, detail } = describeError(noKey('claude'))
    expect(title).toBe('No key is set for Claude. Add it in Settings.')
    expect(title).not.toMatch(/not installed/)
    expect(detail).toBeNull()
  })
  it('falls back to a neutral name for an odd engine value', () => {
    expect(describeError(noKey({ x: 1 })).title).toBe('No key is set for this engine. Add it in Settings.')
  })
  it('keeps the package heading for a real missing package', () => {
    const err = new ApiError(503, { code: 'dependency_unavailable', message: 'OmniVoice is not installed. Install it in Diagnostics.' })
    expect(describeError(err)).toEqual({
      title: 'A tool or package this needs is not installed or not reachable. See Diagnostics.',
      detail: 'OmniVoice is not installed. Install it in Diagnostics.',
    })
  })
})

describe('describeError for a refused export', () => {
  it('shows the server sentence when asked to', () => {
    const err = new ApiError(422, { code: 'validation_error', message: 'There is no narration yet. Create it in Dub first.' })
    expect(describeError(err).detail).toBeNull()
    expect(describeError(err, { serverText: true }).detail).toBe('There is no narration yet. Create it in Dub first.')
    expect(describeError(err, { reasonAsTitle: true })).toEqual({
      title: 'There is no narration yet. Create it in Dub first.',
      detail: null,
    })
  })
  it('keeps the generic heading when the server text looks like it holds a path', () => {
    const err = new ApiError(422, { code: 'validation_error', message: 'Bad file /home/x/a.wav' })
    expect(describeError(err, { reasonAsTitle: true }).title).toMatch(/not valid/)
  })
})
