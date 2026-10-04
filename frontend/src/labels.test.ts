import { describe, expect, it } from 'vitest'

import { capFirst, engineLabel, languageLabel, mediaTypeLabel, statusLabel, sentenceCase } from './labels'

describe('labels', () => {
  it('humanises statuses', () => {
    expect(statusLabel('translated')).toBe('Translated')
    expect(statusLabel('not started')).toBe('Not started')
  })
  it('humanises media types', () => {
    expect(mediaTypeLabel('streamer_vod')).toBe('Streamer VOD')
    expect(mediaTypeLabel('audio_drama')).toBe('Audio drama')
    expect(mediaTypeLabel('asmr')).toBe('ASMR')
  })
  it('names source languages', () => {
    expect(languageLabel('zh')).toBe('Chinese')
    expect(languageLabel('ja')).toBe('Japanese')
    expect(languageLabel('ko')).toBe('Korean')
  })
  it('names engines', () => {
    expect(engineLabel('claude')).toBe('Claude')
    expect(engineLabel('deepseek')).toBe('DeepSeek')
    expect(engineLabel('gemini')).toBe('Gemini')
    expect(engineLabel('ollama')).toBe('Ollama')
    expect(engineLabel('nllb')).toBe('NLLB')
  })
  it('sentence-cases unknown codes and leaves empty values empty', () => {
    expect(mediaTypeLabel('radio_play')).toBe('Radio play')
    expect(engineLabel('some-new_engine')).toBe('Some new engine')
    expect(languageLabel('fr')).toBe('Fr')
    expect(statusLabel(null)).toBe('')
    expect(engineLabel(undefined)).toBe('')
    expect(sentenceCase('  ')).toBe('')
  })
  it('capitalises the first letter of display text', () => {
    expect(capFirst('none saved')).toBe('None saved')
    expect(capFirst('Already fine')).toBe('Already fine')
    expect(capFirst('')).toBe('')
    expect(capFirst('ffmpeg not found')).toBe('ffmpeg not found')
    expect(capFirst('num_ctx auto')).toBe('num_ctx auto')
    expect(capFirst('~3 min')).toBe('~3 min')
  })
})
