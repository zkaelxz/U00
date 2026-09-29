import { describe, expect, it } from 'vitest'

import { advancedSummary, checkOcrImages, ocrBackendOptions, sourceJobIds, type AdvancedValues } from './sourceForm'

const base: AdvancedValues = {
  beam_size: '5', min_silence_ms: '300', vad_threshold: '0.5', hardsub_interval_sec: '1',
  alignment_method: 'whisper_diff', asr_backend_choice: 'whisper', separation_backend: 'auto',
  separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, use_groq: false, prompt: '',
}

describe('advancedSummary', () => {
  it('says defaults when nothing differs', () => {
    expect(advancedSummary(base)).toBe('defaults')
  })
  it('lists only the values that differ', () => {
    expect(advancedSummary({ ...base, beam_size: '8', use_groq: true, prompt: ' x ' })).toBe('beam 8 · Groq · replacement prompt')
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
})
