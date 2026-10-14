import { describe, expect, it } from 'vitest'
import { whisperExclusionReason, whisperSizeChoices, type WhisperExclusions } from './sourceForm'
import { whisperEstimateSeconds } from './stages/transcribeEstimate'

// The shape the server sends: {model: {language: reason}}.
const exclusions: WhisperExclusions = {
  medium: { ja: 'Whisper medium isn\'t recommended for Japanese.' },
  'ja-1.5b': { zh: 'Only for Japanese titles.', ko: 'Only for Japanese titles.' },
  'kotoba-v2': { zh: 'Only for Japanese titles.', ko: 'Only for Japanese titles.' },
}

// What the server's selectable_whisper_sizes sends: the large models and the opt-in Japanese ones.
const SELECTABLE = ['large-v3', 'large-v3-turbo', 'ja-1.5b', 'kotoba-v2']
// A list that includes the sizes the server hides, to show the picker only echoes what it is sent.
const WITH_HIDDEN = [...SELECTABLE, 'tiny', 'base', 'small', 'medium']

const choice = (language: string, current = 'large-v3') =>
  Object.fromEntries(whisperSizeChoices(language, current, exclusions, WITH_HIDDEN).map((c) => [c.size, c.reason]))

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

  it('lists exactly what the server sends, so a new entry needs no frontend list edit', () => {
    const sizes = whisperSizeChoices('ko', 'large-v3', exclusions, [...SELECTABLE, 'anime-whisper']).map((c) => c.size)
    expect(sizes).toEqual([...SELECTABLE, 'anime-whisper'])
  })

  it('offers only the large models the server lists, and the small ones only if it ever lists them', () => {
    const plain = whisperSizeChoices('zh', 'large-v3', exclusions, SELECTABLE).map((c) => c.size)
    expect(plain).not.toContain('medium')
    expect(plain).not.toContain('tiny')
    expect(plain.slice(0, 2)).toEqual(['large-v3', 'large-v3-turbo'])
    expect(whisperSizeChoices('zh', 'large-v3', exclusions, WITH_HIDDEN).map((c) => c.size)).toContain('medium')
  })

  it('falls back to the two large models when an older server sends no list', () => {
    expect(whisperSizeChoices('zh', 'large-v3').map((c) => c.size)).toEqual(['large-v3', 'large-v3-turbo'])
  })

  it('keeps a saved hidden size as the first, selected-able option flagged hidden, never migrated', () => {
    const c = whisperSizeChoices('zh', 'medium', exclusions, SELECTABLE)
    expect(c[0]).toEqual({ size: 'medium', reason: null, hidden: true })
    expect(c.filter((x) => x.hidden)).toHaveLength(1)
    // The same size is not hidden if the server lists it.
    expect(whisperSizeChoices('zh', 'medium', exclusions, WITH_HIDDEN).some((x) => x.hidden)).toBe(false)
  })

  it('keeps an unknown saved size visible and selectable', () => {
    const c = whisperSizeChoices('zh', 'large-v2', exclusions, SELECTABLE)
    expect(c[0]).toEqual({ size: 'large-v2', reason: null, hidden: true })
  })

  it('estimates large-v3 as slower than turbo on the CPU', () => {
    const run = (whisperSize: string) => whisperEstimateSeconds({ audioSeconds: 600, whisperSize, useGpu: false })!
    expect(run('large-v3').high_s).toBeGreaterThan(run('large-v3-turbo').high_s)
  })

  it('has a time estimate for the Japanese model', () => {
    expect(whisperEstimateSeconds({ audioSeconds: 600, whisperSize: 'ja-1.5b', useGpu: true, fastMode: false })).not.toBeNull()
    expect(whisperEstimateSeconds({ audioSeconds: 600, whisperSize: 'kotoba-v2', useGpu: true, fastMode: false })).not.toBeNull()
  })
})
