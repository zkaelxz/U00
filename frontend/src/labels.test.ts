import { describe, expect, it } from 'vitest'

import { engineLabel, languageLabel, mediaTypeLabel, statusLabel, titleCase } from './labels'

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
    expect(engineLabel('deepl')).toBe('DeepL')
    expect(engineLabel('google')).toBe('Google')
    expect(engineLabel('nllb')).toBe('NLLB')
    expect(engineLabel('libretranslate')).toBe('LibreTranslate')
  })
  it('title-cases unknown codes and leaves empty values empty', () => {
    expect(mediaTypeLabel('radio_play')).toBe('Radio Play')
    expect(engineLabel('some-new_engine')).toBe('Some New Engine')
    expect(languageLabel('fr')).toBe('Fr')
    expect(statusLabel(null)).toBe('')
    expect(engineLabel(undefined)).toBe('')
    expect(titleCase('  ')).toBe('')
  })
})
