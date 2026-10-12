import { describe, expect, it } from 'vitest'

import type { TranslateRunConfig } from '../../../types/translateStage'
import { initialForm } from '../translateForm'
import { buildRetryBody, failedLinesText, retryBlocker, retryLabel, splitFailed } from './followupModel'

const config = {
  default_style_preset: 'natural',
  style_presets: [{ key: 'natural', label: 'Natural' }],
  locales: ['en-US'],
  defaults: { context_window: 5, context_window_ahead: 2, batch_size: 20 },
  translation_engine: 'claude',
  batch_size_defaults: {},
} as unknown as TranslateRunConfig

describe('failed lines notice text', () => {
  it('says lines are untranslated only when they are empty', () => {
    expect(failedLinesText([1, 2, 3, 9], [])).toBe('lines 1-3, 9 are still untranslated')
    expect(failedLinesText([5], [])).toBe('line 5 is still untranslated')
  })
  it('says lines holding older English kept their earlier translation', () => {
    expect(failedLinesText([], [468, 469, 1843])).toBe('lines 468-469, 1843 kept their earlier translation')
    expect(failedLinesText([], [7])).toBe('line 7 kept its earlier translation')
    expect(failedLinesText([3], [7])).toBe('line 3 is still untranslated; line 7 kept its earlier translation')
  })
  it('treats every failed line as untranslated when the server does not say', () => {
    expect(splitFailed([1, 2])).toEqual({ empty: [1, 2], kept: [] })
    expect(splitFailed([1, 2], [], [])).toEqual({ empty: [1, 2], kept: [] })
    expect(splitFailed([1, 2], [2], [1])).toEqual({ empty: [2], kept: [1] })
  })
  it('labels the retry button with the line count', () => {
    expect(retryLabel(61)).toBe('Retry these 61 lines')
    expect(retryLabel(1)).toBe('Retry this line')
  })
})

describe('retry body', () => {
  const f = { ...initialForm(config), engine: 'claude', model: 'claude-sonnet-5-5', batch_size: '40', force: true, forceConfirmed: true }
  it('carries the form engine and model but no lines, force, bulk or reflect', () => {
    const body = buildRetryBody({ ...f, reflect: true, bulk: true })
    expect(body).toMatchObject({ engine: 'claude', model: 'claude-sonnet-5-5', batch_size: 40 })
    for (const key of ['line_ids', 'force_retranslate', 'bulk', 'reflect']) expect(body).not.toHaveProperty(key)
  })
  it('is blocked by an invalid form field, not by Reflect or Bulk', () => {
    expect(retryBlocker({ ...f, reflect: true, bulk: true }, 'claude')).toBeNull()
    expect(retryBlocker({ ...f, batch_size: '999' }, 'claude')).toMatch(/Batch size/)
  })
})
