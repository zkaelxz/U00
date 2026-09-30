import { describe, expect, it } from 'vitest'

import {
  BULK_HELP,
  bulkBlocker,
  bulkStartedText,
  effectiveEngine,
  reviewBulkAvailable,
  reviewStartBody,
} from './reviewBulk'
import { EMPTY_CHECK_FORM } from './reviewResults'

// bulk_supported_engines as the server sends it: free tier off, then on.
const PAID = ['claude', 'gemini', 'deepseek']
const FREE_TIER = ['claude', 'deepseek']

describe('Bulk review engine gating (R49)', () => {
  it('allows Claude, and Gemini only with the free tier off', () => {
    expect(reviewBulkAvailable('claude', PAID)).toBe(true)
    expect(reviewBulkAvailable('gemini', PAID)).toBe(true)
    expect(reviewBulkAvailable('gemini', FREE_TIER)).toBe(false)
    expect(reviewBulkAvailable('claude', FREE_TIER)).toBe(true)
  })
  it('refuses DeepSeek (bulk translation only) and every other engine', () => {
    for (const e of ['deepseek', 'openai', 'ollama', 'nllb', '']) expect(reviewBulkAvailable(e, PAID)).toBe(false)
  })
  it('refuses when the server lists no bulk engines', () => {
    expect(reviewBulkAvailable('claude', [])).toBe(false)
  })
  it('names the fix when Bulk cannot be used', () => {
    expect(bulkBlocker('claude', PAID)).toBeNull()
    expect(bulkBlocker('gemini', FREE_TIER)).toMatch(/^Still needed for Bulk: Gemini's free tier turned off in Settings/)
    expect(bulkBlocker('deepseek', PAID)).toBe('Still needed for Bulk: Claude or Gemini as the engine (Check options).')
  })
  it('uses the picked engine, else the drama default', () => {
    expect(effectiveEngine({ engine: '' }, 'gemini')).toBe('gemini')
    expect(effectiveEngine({ engine: ' claude ' }, 'gemini')).toBe('claude')
  })
})

describe('Bulk start body (R49)', () => {
  const claude = { ...EMPTY_CHECK_FORM, engine: 'claude' }
  it('sends bulk only for the check whose switch is on', () => {
    expect(reviewStartBody('consistency', claude, { consistency: true }, 'gemini', PAID)).toEqual({ engine: 'claude', bulk: true })
    expect(reviewStartBody('flag', claude, { consistency: true }, 'gemini', PAID)).toEqual({ engine: 'claude' })
    expect(reviewStartBody('notes', EMPTY_CHECK_FORM, { notes: false }, 'claude', PAID)).toEqual({})
  })
  it('keeps the other options with bulk', () => {
    const f = { engine: '', model: 'm1', audioCues: false }
    expect(reviewStartBody('emotion', f, { emotion: true }, 'claude', PAID)).toEqual({ model: 'm1', use_audio_cues: false, bulk: true })
  })
  it('never sends bulk for an engine the server would refuse', () => {
    expect(reviewStartBody('consistency', EMPTY_CHECK_FORM, { consistency: true }, 'deepseek', PAID)).toEqual({})
    expect(reviewStartBody('consistency', EMPTY_CHECK_FORM, { consistency: true }, 'gemini', FREE_TIER)).toEqual({})
    expect(reviewStartBody('consistency', { ...EMPTY_CHECK_FORM, engine: 'openai' }, { consistency: true }, 'claude', PAID)).toEqual({
      engine: 'openai',
    })
  })
})

describe('Bulk copy', () => {
  it('says the price, the wait and which edits are refused', () => {
    expect(BULK_HELP).toMatch(/Half price/)
    expect(BULK_HELP).toMatch(/24 hours/)
    expect(BULK_HELP).toMatch(/by its id/)
    for (const w of ['adding', 'deleting', 'merging', 'splitting', 're-segmenting', 'restoring a version', 'deleting the drama'])
      expect(BULK_HELP).toContain(w)
    const t = bulkStartedText('Consistency check', 1)
    expect(t).toMatch(/^Consistency check: sent as a bulk batch at half price \(1 line\)\./)
    expect(t).toMatch(/at most 24 hours/)
    expect(t).toMatch(/structural edits .* are paused/)
    expect(bulkStartedText('Emotion tags', 4)).toContain('(4 lines)')
  })
})
