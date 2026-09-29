import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DramaDetail } from '../../api/types'
import {
  checkUploadFile,
  loadSourceForm,
  parseExpectedSpeakers,
  saveSourceForm,
  sourceJobIds,
  validateConfig,
  whisperModelWarning,
} from './sourceForm'
import { STAGE_IDS, isStageId, parseStage } from './stages'
import { pickForId } from './useDrama'

describe('stage parsing', () => {
  it('accepts known stages and falls back to source for unknown ones', () => {
    for (const s of STAGE_IDS) expect(parseStage(s)).toBe(s)
    expect(parseStage('nope')).toBe('source')
    expect(parseStage('')).toBe('source')
    expect(isStageId('Source')).toBe(false)
  })
})

describe('drama switch state reset', () => {
  it('never returns state fetched for another drama', () => {
    const state = { id: 1, drama: { id: 1 } as DramaDetail }
    expect(pickForId(state, 1)).toBe(state)
    expect(pickForId(state, 2)).toBeNull()
    expect(pickForId(null, 1)).toBeNull()
  })
})

describe('upload pre-check', () => {
  const MB = 1024 * 1024
  it('accepts a supported file within the limit', () => {
    expect(checkUploadFile('Clip.MP3', 5 * MB, 10)).toBeNull()
  })
  it('rejects unsupported extensions, empty and oversized files', () => {
    expect(checkUploadFile('notes.txt', 10, 10)).toMatch(/not supported/)
    expect(checkUploadFile('noext', 10, 10)).toMatch(/not supported/)
    expect(checkUploadFile('a.wav', 0, 10)).toMatch(/empty/)
    expect(checkUploadFile('a.wav', 10 * MB + 1, 10)).toMatch(/10 MB/)
  })
})

describe('config validation', () => {
  it('mirrors the server ranges', () => {
    expect(validateConfig({ beam_size: 5, min_silence_ms: 300, vad_threshold: 0.9 })).toBeNull()
    expect(validateConfig({ beam_size: 0 })).toMatch(/beam size/)
    expect(validateConfig({ beam_size: 2.5 })).toMatch(/whole number/)
    expect(validateConfig({ min_silence_ms: 3001 })).toMatch(/300 and 3000/)
    expect(validateConfig({ vad_threshold: 0.05 })).toMatch(/0.1 and 0.9/)
    expect(validateConfig({ hardsub_interval_sec: Number.NaN })).toMatch(/0.5 and 3/)
  })
  it('parses expected speakers', () => {
    expect(parseExpectedSpeakers('')).toBeUndefined()
    expect(parseExpectedSpeakers('0')).toBe(0)
    expect(parseExpectedSpeakers('20')).toBe(20)
    expect(parseExpectedSpeakers('21')).toBeNull()
    expect(parseExpectedSpeakers('1.5')).toBeNull()
  })
})

describe('whisper model warning', () => {
  it('warns only for large-v3-turbo on Japanese/Korean', () => {
    expect(whisperModelWarning('large-v3-turbo', 'ja')).toMatch(/weaker on Japanese and Korean/)
    expect(whisperModelWarning('large-v3-turbo', 'ko')).not.toBe('')
    expect(whisperModelWarning('large-v3-turbo', 'zh')).toBe('')
    expect(whisperModelWarning('large-v3', 'ja')).toBe('')
  })
})

describe('source form persistence', () => {
  afterEach(() => vi.unstubAllGlobals())
  const memory = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  }
  const state = { language: 'ja', script: '', transcriptText: 'x', runDiarize: true, speakers: '2', extraNames: 'p' }

  it('round-trips per drama without leaking across dramas', () => {
    vi.stubGlobal('sessionStorage', memory())
    saveSourceForm(1, state)
    expect(loadSourceForm(1)).toEqual(state)
    expect(loadSourceForm(2)).toEqual({})
  })
  it('ignores corrupt data and survives throwing storage', () => {
    vi.stubGlobal('sessionStorage', { getItem: () => '{"language":5,"extraNames":"ok"}', setItem: () => undefined })
    expect(loadSourceForm(1)).toEqual({ extraNames: 'ok' })
    const boom = () => {
      throw new Error('blocked')
    }
    vi.stubGlobal('sessionStorage', { getItem: boom, setItem: boom })
    expect(loadSourceForm(1)).toEqual({})
    expect(() => saveSourceForm(1, state)).not.toThrow()
  })
  it('names the reattachable job ids', () => {
    expect(sourceJobIds(7)).toEqual(['transcribe_7', 'diarize_7', 'ocrchapter_7', 'extract_audio_7', 'urlmedia_7'])
  })
})
