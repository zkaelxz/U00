import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DramaDetail } from '../../api/types'
import { draftKey, DRAFT_VERSION, pickDraft, readDraft, writeDraft } from '../../hooks/useStageDraft'
import {
  checkUploadFile,
  isUploadLimitProblem,
  parseExpectedSpeakers,
  parseSpeakerHints,
  sourceJobIds,
  SOURCE_DRAFT_SHAPE,
  TRANSCRIBE_DRAFT_SHAPE,
  TRANSCRIBE_DRAFT_STAGE,
  transcribeExtraNames,
  URL_DRAFT_SHAPE,
  validateConfig,
  whisperModelWarning,
} from './sourceForm'
import { STAGE_IDS, isStageId, nextAction, parseStage, stageCount, stageStates, startStage } from './stages'
import { pickForId } from './useDrama'

describe('stage parsing', () => {
  it('accepts known stages and falls back to source for unknown ones', () => {
    for (const s of STAGE_IDS) expect(parseStage(s)).toBe(s)
    expect(parseStage('nope')).toBe('source')
    expect(parseStage('')).toBe('source')
    expect(isStageId('Source')).toBe(false)
  })
})

describe('start stage (P17)', () => {
  it('keeps a stage named in the URL', () => {
    expect(startStage('review', { stage: 'export' }, false)).toBe('review')
    expect(startStage('bogus', null, false)).toBe('source')
  })
  it('opens the reported stage when the URL names none', () => {
    expect(startStage(null, null, false)).toBeNull()
    expect(startStage(null, { stage: 'translate' }, false)).toBe('translate')
    expect(startStage(null, { stage: 'weird' }, false)).toBe('source')
    expect(startStage(null, null, true)).toBe('source')
  })
})

describe('stage states (P16)', () => {
  it('keeps known stages and states only', () => {
    expect(
      stageStates([
        { key: 'source', state: 'done' },
        { key: 'translate', state: 'current' },
        { key: 'dub', state: 'optional' },
        { key: 'other', state: 'done' },
        { key: 'export', state: 'weird' },
      ]),
    ).toEqual({ source: 'done', translate: 'current', dub: 'optional' })
    expect(stageStates(undefined)).toEqual({})
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
    expect(checkUploadFile('a.wav', 10 * MB + 1, 10)).toMatch(/10 MB upload limit/)
    expect(isUploadLimitProblem(checkUploadFile('a.wav', 10 * MB + 1, 10) ?? '')).toBe(true)
    expect(isUploadLimitProblem('That file is empty.')).toBe(false)
  })
})

describe('config validation', () => {
  it('mirrors the server ranges', () => {
    expect(validateConfig({ beam_size: 5, min_silence_ms: 300, vad_threshold: 0.9 })).toBeNull()
    expect(validateConfig({ beam_size: 0 })).toMatch(/Beam size/)
    expect(validateConfig({ beam_size: 2.5 })).toMatch(/whole number/)
    expect(validateConfig({ min_silence_ms: 99 })).toMatch(/100 and 3000/)
    expect(validateConfig({ min_silence_ms: 100 })).toBeNull()
    expect(validateConfig({ min_silence_ms: 3000 })).toBeNull()
    expect(validateConfig({ min_silence_ms: 3001 })).toMatch(/100 and 3000/)
    expect(validateConfig({ min_pause_sec: 0.35 })).toBeNull()
    expect(validateConfig({ min_pause_sec: 0.09 })).toMatch(/0.1 and 2/)
    expect(validateConfig({ min_pause_sec: 0.1 })).toBeNull()
    expect(validateConfig({ min_pause_sec: 2 })).toBeNull()
    expect(validateConfig({ min_pause_sec: 2.01 })).toMatch(/0.1 and 2/)
    expect(validateConfig({ min_pause_sec: Number.NaN })).toMatch(/0.1 and 2/)
    expect(validateConfig({ vad_threshold: 0.05 })).toMatch(/0.1 and 0.9/)
    expect(validateConfig({ hardsub_interval_sec: Number.NaN })).toMatch(/0.5 and 3/)
    expect(validateConfig({ hallucination_silence_sec: 0 })).toBeNull()
    expect(validateConfig({ hallucination_silence_sec: 0.2 })).toMatch(/0.5 and 10/)
  })
  it('parses expected speakers', () => {
    expect(parseExpectedSpeakers('')).toBeUndefined()
    expect(parseExpectedSpeakers('0')).toBe(0)
    expect(parseExpectedSpeakers('20')).toBe(20)
    expect(parseExpectedSpeakers('21')).toBeNull()
    expect(parseExpectedSpeakers('1.5')).toBeNull()
  })
  it('parses a speaker range and rejects bad combinations', () => {
    expect(parseSpeakerHints('', '', '')).toEqual({ expected: undefined, min: undefined, max: undefined })
    expect(parseSpeakerHints('3', '', '')).toEqual({ expected: 3, min: undefined, max: undefined })
    expect(parseSpeakerHints('', '2', '4')).toEqual({ expected: undefined, min: 2, max: 4 })
    expect(parseSpeakerHints('0', '2', '')).toEqual({ expected: 0, min: 2, max: undefined })
    expect(parseSpeakerHints('', '4', '2')).toMatch(/more than max/)
    expect(parseSpeakerHints('', '0', '')).toMatch(/1 to 20/)
    expect(parseSpeakerHints('3', '2', '4')).toMatch(/not both/)
    expect(parseSpeakerHints('x', '', '')).toMatch(/0 to 20/)
  })
})

describe('whisper model warning', () => {
  it('notes the measured Korean and Chinese gaps for turbo, and nothing for Japanese', () => {
    expect(whisperModelWarning('large-v3-turbo', 'ko')).toMatch(/half a point fewer .* twice as slow/)
    expect(whisperModelWarning('large-v3-turbo', 'zh')).toMatch(/tests disagree/)
    expect(whisperModelWarning('large-v3-turbo', 'ja')).toBe('')
    expect(whisperModelWarning('large-v3', 'ko')).toBe('')
    expect(whisperModelWarning('medium', 'ko')).toBe('')
  })
})

describe('source stage drafts', () => {
  afterEach(() => vi.unstubAllGlobals())
  const memory = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }
  const state = {
    language: 'ja', script: '', transcriptText: 'x', runDiarize: true, speakers: '2', minSpeakers: '', maxSpeakers: '',
    extraNames: 'p', override: 'full prompt',
  }

  it('the Transcribe draft round-trips per drama, with the prompt override and the changed Advanced values', () => {
    const s = memory()
    writeDraft(s, 1, TRANSCRIBE_DRAFT_STAGE, { ...state, advanced: { beam_size: '8' } })
    const raw = readDraft(s, 1, TRANSCRIBE_DRAFT_STAGE)
    expect(pickDraft(raw, TRANSCRIBE_DRAFT_SHAPE)).toEqual(state)
    expect(raw?.advanced).toEqual({ beam_size: '8' })
    expect(pickDraft(readDraft(s, 2, TRANSCRIBE_DRAFT_STAGE), TRANSCRIBE_DRAFT_SHAPE)).toEqual({})
  })
  it('a wrong-typed value falls back to the default', () => {
    expect(pickDraft({ language: 5, runDiarize: 'yes', extraNames: 'ok' }, TRANSCRIBE_DRAFT_SHAPE)).toEqual({ extraNames: 'ok' })
    expect(pickDraft({ from: 'url' }, SOURCE_DRAFT_SHAPE)).toEqual({ from: 'url' })
    expect(pickDraft({ url: 'https://x', audioOnly: 'no' }, URL_DRAFT_SHAPE)).toEqual({ url: 'https://x' })
  })
  it('the Compare prompt starts from the names kept on the Transcribe stage, or blank', () => {
    const s = memory()
    vi.stubGlobal('window', { localStorage: s })
    expect(transcribeExtraNames(3)).toBe('')
    s.setItem(draftKey(3, TRANSCRIBE_DRAFT_STAGE), JSON.stringify({ v: DRAFT_VERSION, values: { extraNames: '沈清疑' } }))
    expect(transcribeExtraNames(3)).toBe('沈清疑')
    expect(transcribeExtraNames(4)).toBe('')
    const boom = () => {
      throw new Error('blocked')
    }
    vi.stubGlobal('window', { localStorage: { getItem: boom, setItem: boom, removeItem: boom } })
    expect(transcribeExtraNames(3)).toBe('')
  })
  it('names the reattachable job ids', () => {
    expect(sourceJobIds(7)).toEqual(['transcribe_7', 'diarize_7', 'ocrchapter_7', 'extract_audio_7', 'urlmedia_7'])
  })
})

describe('stepper counts (§3.3)', () => {
  const p = { line_count: 40, untranslated_count: 32, flagged_count: 12 }
  it('shows untranslated on Translate and flagged on Review', () => {
    expect(stageCount('translate', p)).toBe('32 left')
    expect(stageCount('review', p)).toBe('12 flagged')
    expect(stageCount('source', p)).toBeNull()
    expect(stageCount('export', p)).toBeNull()
  })
  it('shows nothing with no progress, no lines or zero counts', () => {
    expect(stageCount('translate', null)).toBeNull()
    expect(stageCount('translate', { ...p, line_count: 0 })).toBeNull()
    expect(stageCount('review', { ...p, flagged_count: 0 })).toBeNull()
  })
})

describe('next action', () => {
  const prog = (states: Record<string, string>, untranslated = 0, flagged = 0) => ({
    untranslated_count: untranslated,
    flagged_count: flagged,
    stages: Object.entries(states).map(([key, state]) => ({ key, state })),
  })
  it('names the translate step with the untranslated count', () => {
    expect(nextAction('source', prog({ source: 'done', translate: 'current' }, 32))).toEqual({ stage: 'translate', label: 'Translate 32 lines' })
    expect(nextAction('source', prog({ source: 'done', translate: 'current' }, 1))?.label).toBe('Translate 1 line')
  })
  it('names the review step with the flagged count, or just Review', () => {
    expect(nextAction('translate', prog({ translate: 'done', review: 'current' }, 0, 12))?.label).toBe('Review 12 flagged')
    expect(nextAction('translate', prog({ translate: 'done', review: 'current' }))?.label).toBe('Review')
  })
  it('skips a blocked stage and stops after the last one', () => {
    expect(nextAction('review', prog({ review: 'done', dub: 'blocked', export: 'pending' }))).toEqual({ stage: 'export', label: 'Export' })
    expect(nextAction('export', prog({ export: 'done' }))).toBeNull()
  })
  it('shows nothing until the open stage is done or progress is known', () => {
    expect(nextAction('source', prog({ source: 'current', translate: 'pending' }, 5))).toBeNull()
    expect(nextAction('source', null)).toBeNull()
    expect(nextAction(null, prog({ source: 'done' }))).toBeNull()
  })
})
