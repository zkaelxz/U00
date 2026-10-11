import { describe, expect, it } from 'vitest'
import { whisperExclusionReason, whisperSizeChoices, type WhisperExclusions } from './sourceForm'
import { whisperEstimateSeconds } from './stages/transcribeEstimate'

// The shape the server sends: {model: {language: reason}}.
const exclusions: WhisperExclusions = {
  medium: { ja: 'Whisper medium isn\'t recommended for Japanese.' },
  'ja-1.5b': { zh: 'Only for Japanese titles.', ko: 'Only for Japanese titles.' },
  'kotoba-v2': { zh: 'Only for Japanese titles.', ko: 'Only for Japanese titles.' },
}

const choice = (language: string, current = 'large-v3-turbo') =>
  Object.fromEntries(whisperSizeChoices(language, current, exclusions).map((c) => [c.size, c.reason]))

describe('Whisper model choices per language', () => {
  it.each([
    ['ja', 'medium', true], ['zh', 'medium', false], ['ko', 'medium', false],
    ['ja', 'ja-1.5b', false], ['zh', 'ja-1.5b', true], ['ko', 'ja-1.5b', true],
    ['ja', 'kotoba-v2', false], ['zh', 'kotoba-v2', true], ['ko', 'kotoba-v2', true],
    ['ja', 'large-v3', false], ['zh', 'large-v3-turbo', false], ['ko', 'small', false],
  ])('%s / %s is disabled: %s', (language, size, disabled) => {
    const c = choice(language)
    expect(size in c).toBe(true)
    expect(c[size] !== null).toBe(disabled)
  })

  it('shows the server reason for a disabled choice', () => {
    expect(choice('ja').medium).toBe(exclusions.medium.ja)
    expect(whisperExclusionReason(exclusions, 'ja-1.5b', 'ko')).toBe('Only for Japanese titles.')
    expect(choice('zh')['kotoba-v2']).toBe('Only for Japanese titles.')
    expect(whisperExclusionReason(exclusions, 'medium', 'zh')).toBeNull()
    expect(whisperExclusionReason(undefined, 'medium', 'ja')).toBeNull()
  })

  it('lists a model that only the server table knows about, so a new entry needs no frontend list edit', () => {
    const extra = { ...exclusions, 'anime-whisper': { zh: 'Japanese only.', ko: 'Japanese only.' } }
    const sizes = whisperSizeChoices('ko', 'large-v3-turbo', extra).map((c) => c.size)
    expect(sizes).toContain('anime-whisper')
  })

  it('keeps an unknown saved size visible and selectable', () => {
    const c = whisperSizeChoices('zh', 'large-v2', exclusions)
    expect(c[0]).toEqual({ size: 'large-v2', reason: null })
  })

  it('has a time estimate for the Japanese model', () => {
    expect(whisperEstimateSeconds({ audioSeconds: 600, whisperSize: 'ja-1.5b', useGpu: true, fastMode: false })).not.toBeNull()
    expect(whisperEstimateSeconds({ audioSeconds: 600, whisperSize: 'kotoba-v2', useGpu: true, fastMode: false })).not.toBeNull()
  })
})
