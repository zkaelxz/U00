import { describe, expect, it } from 'vitest'

import type { TranslateRunConfig } from '../../../types/translateStage'
import { isSourceChanged, retranslateBody, sourceChangedText } from './sourceChanged'

const config = (over: Partial<TranslateRunConfig> = {}) => ({
  translation_engine: 'deepseek',
  default_style_preset: 'audio_drama',
  default_style_note: 'Keep honorifics.',
  default_locale: 'en-GB',
  defaults: { context_window: 10, context_window_ahead: 6, batch_size: 30 },
  default_female_pronouns: true,
  include_genre_notes: null,
  ...over,
}) as TranslateRunConfig

describe('source changed', () => {
  it('names the count in plain words', () => {
    expect(sourceChangedText(1)).toBe('1 line changed in the source since it was translated')
    expect(sourceChangedText(3)).toBe('3 lines changed in the source since they were translated')
  })

  it('recognises only its own flag', () => {
    expect(isSourceChanged({ flag: 'source_changed' })).toBe(true)
    expect(isSourceChanged({ flag: 'reading_speed' })).toBe(false)
    expect(isSourceChanged({ flag: null })).toBe(false)
  })

  it('retranslates exactly the given ids with the Translate form’s starting options, never bulk or force', () => {
    const body = retranslateBody(config(), [4, 9])
    expect(body).toEqual({
      line_ids: [4, 9], engine: 'deepseek', style_preset: 'audio_drama', style_note: 'Keep honorifics.',
      locale: 'en-GB', context_window: 10, context_window_ahead: 6, batch_size: 30, default_female_pronouns: true,
    })
    expect(body).not.toHaveProperty('bulk')
    expect(body).not.toHaveProperty('force_retranslate')
    expect(retranslateBody(config({ default_locale: undefined, default_style_note: undefined }), [1]))
      .toMatchObject({ locale: 'en-US', style_note: '' })
  })
})
