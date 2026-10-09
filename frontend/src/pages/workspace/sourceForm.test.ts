import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import {
  advancedSummary,
  checkOcrImages,
  isDirectAudioUrl,
  isVideoFile,
  ocrBackendOptions,
  runOptionProblem,
  runProblemFromError,
  sourceJobIds,
  type AdvancedValues,
} from './sourceForm'

const base: AdvancedValues = {
  beam_size: '5', min_silence_ms: '300', min_pause_sec: '0.35', vad_threshold: '0.5', hallucination_silence_sec: '0', hardsub_interval_sec: '1',
  alignment_method: 'whisper_diff', asr_backend_choice: 'whisper', separation_backend: 'auto',
  separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false,
  whisper_repeat_guard: false, split_by_sentences: false, use_groq: false, prompt: '',
}

describe('advancedSummary', () => {
  it('says defaults when nothing differs', () => {
    expect(advancedSummary(base)).toBe('defaults')
  })
  it('lists only the values that differ', () => {
    expect(advancedSummary({ ...base, beam_size: '8', use_groq: true, prompt: ' x ' })).toBe('beam 8 · Groq · replacement prompt')
  })
  it('mentions a changed split pause only when it differs from 0.35', () => {
    expect(advancedSummary({ ...base, min_pause_sec: '0.5' })).toBe('split pause 0.5 s')
    expect(advancedSummary({ ...base, min_pause_sec: '0.350' })).toBe('defaults')
    expect(advancedSummary({ ...base, sensitivity_preset: 'sensitive' })).toBe('more sensitive')
    expect(advancedSummary({ ...base, sensitivity_preset: 'normal' })).toBe('defaults')
  })
  it('mentions the hallucination guard only when it is on', () => {
    expect(advancedSummary({ ...base, hallucination_silence_sec: '3' })).toBe('hallucination guard 3 s')
  })
  it('mentions the repeat guard and sentence lines when on', () => {
    expect(advancedSummary({ ...base, whisper_repeat_guard: true, split_by_sentences: true }))
      .toBe('repeat guard · lines by sentence')
  })
})

describe('chapter OCR helpers', () => {
  it('offers backends per source language', () => {
    expect(ocrBackendOptions('zh')).toEqual(['tesseract', 'paddle'])
    expect(ocrBackendOptions('ja')).toEqual(['manga_ocr', 'tesseract'])
    expect(ocrBackendOptions('ko')).toEqual(['tesseract'])
    expect(ocrBackendOptions(null)).toEqual(['tesseract', 'paddle'])
    expect(ocrBackendOptions('fr')).toEqual(['tesseract'])
  })
  it('validates image names', () => {
    expect(checkOcrImages([])).toBeNull()
    expect(checkOcrImages(['a.PNG', 'b.jpeg'])).toBeNull()
    expect(checkOcrImages(['a.png', 'b.gif'])).toContain('b.gif')
    expect(checkOcrImages(Array(201).fill('a.png'))).toContain('200')
  })
  it('reattaches to the OCR job', () => {
    expect(sourceJobIds(3)).toContain('ocrchapter_3')
  })
  it('reattaches to a URL download', () => {
    expect(sourceJobIds(3)).toContain('urlmedia_3')
  })
})

describe('runOptionProblem', () => {
  it('flags forced alignment on a Whisper-only drama', () => {
    expect(runOptionProblem('whisper', 'qwen3_forced_align')?.field).toBe('alignment_method')
    expect(runOptionProblem('have_transcript', 'qwen3_forced_align')).toBeNull()
  })
  it('has nothing to flag for Whisper-diff on a Whisper-only drama', () => {
    expect(runOptionProblem('whisper', 'whisper_diff')).toBeNull()
  })
})

describe('runProblemFromError', () => {
  const err = (code: string, message: string) => new ApiError(422, { code, message })
  it('maps the server sentence to the field it names', () => {
    const msg = "Qwen3 forced alignment needs a transcript to align, but this drama is in Whisper-text-only mode."
    expect(runProblemFromError(err('validation_error', msg))).toEqual({ field: 'alignment_method', message: msg })
    expect(runProblemFromError(err('validation_error', 'Use either an exact speaker count or a min/max range, not both.'))?.field).toBe('speakers')
  })
  it('ignores other codes, unknown sentences and path-like text', () => {
    expect(runProblemFromError(err('conflict', 'forced alignment'))).toBeNull()
    expect(runProblemFromError(err('validation_error', 'Invalid transcribe options.'))).toBeNull()
    expect(runProblemFromError(err('validation_error', 'forced alignment failed at /home/me/x'))).toBeNull()
    expect(runProblemFromError(null)).toBeNull()
  })
})

describe('isVideoFile', () => {
  it('tells a video upload from an audio one by extension, any case', () => {
    expect(isVideoFile('Episode 1.WEBM')).toBe(true)
    expect(isVideoFile('clip.mkv')).toBe(true)
    expect(isVideoFile('dub.mp3')).toBe(false)
    expect(isVideoFile('mp4.wav')).toBe(false)
  })
})

describe('isDirectAudioUrl', () => {
  it('reads the extension of the URL path, as the server does', () => {
    expect(isDirectAudioUrl('https://a.example/x/ep.MP3?t=1')).toBe(true)
    expect(isDirectAudioUrl(' https://a.example/x/ep.flac ')).toBe(true)
    expect(isDirectAudioUrl('https://a.example/x/ep.mp4')).toBe(false)
    expect(isDirectAudioUrl('https://a.example/watch?v=x.mp3')).toBe(false)
    expect(isDirectAudioUrl('https://a.example/.mp3')).toBe(false)
    expect(isDirectAudioUrl('not a url')).toBe(false)
  })
})
