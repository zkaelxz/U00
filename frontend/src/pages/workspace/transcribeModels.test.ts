import { describe, expect, it } from 'vitest'
import { whisperSizeChoices } from './sourceForm'
import { whisperEstimateSeconds } from './stages/transcribeEstimate'

describe('Japanese-only Whisper model', () => {
  it('is offered for a Japanese title only', () => {
    expect(whisperSizeChoices('ja', 'large-v3-turbo')).toContain('ja-1.5b')
    for (const lang of ['zh', 'ko']) expect(whisperSizeChoices(lang, 'large-v3-turbo')).not.toContain('ja-1.5b')
  })

  it('is never listed when the saved choice is it but the language is not Japanese', () => {
    expect(whisperSizeChoices('ko', 'ja-1.5b')).not.toContain('ja-1.5b')
  })

  it('keeps an unknown saved size visible', () => {
    expect(whisperSizeChoices('zh', 'large-v2')[0]).toBe('large-v2')
  })

  it('has a time estimate', () => {
    expect(whisperEstimateSeconds({ audioSeconds: 600, whisperSize: 'ja-1.5b', useGpu: true, fastMode: false })).not.toBeNull()
  })
})
