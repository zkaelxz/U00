import { describe, expect, it } from 'vitest'

import type { TranslateRunConfig } from '../../types/translateStage'
import {
  buildEstimateParams,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  initialForm,
  parseCap,
  reflectAvailable,
  splitLines,
  validateRun,
} from './translateForm'

const config = {
  default_style_preset: 'natural',
  locales: ['en-GB', 'en-US'],
  defaults: { context_window: 5, context_window_ahead: 2, batch_size: 20 },
} as TranslateRunConfig

describe('translate form', () => {
  it('starts from the config defaults', () => {
    const f = initialForm(config)
    expect(f).toMatchObject({ locale: 'en-US', batch_size: '20', context_window: '5', style_preset: 'natural' })
    expect(validateRun(f, 'ollama')).toBeNull()
  })

  it('validates ranges, cap, chain and the force confirmation', () => {
    const base = initialForm(config)
    expect(validateRun({ ...base, batch_size: '0' }, 'x')).toMatch(/Batch size/)
    expect(validateRun({ ...base, batch_size: '201' }, 'x')).toMatch(/Batch size/)
    expect(validateRun({ ...base, context_window: '101' }, 'x')).toMatch(/Context window must/)
    expect(validateRun({ ...base, context_window_ahead: '-1' }, 'x')).toMatch(/ahead/)
    expect(validateRun({ ...base, cost_cap: '-2' }, 'x')).toMatch(/Cost cap/)
    expect(validateRun({ ...base, fallbacks: ['a', 'b', 'c', 'd'] }, 'x')).toMatch(/At most 3/)
    expect(validateRun({ ...base, fallbacks: [''] }, 'x')).toMatch(/every fallback/)
    expect(validateRun({ ...base, fallbacks: ['x'] }, 'x')).toMatch(/twice/)
    expect(validateRun({ ...base, engine: 'a', fallbacks: ['b', 'b'] }, 'x')).toMatch(/twice/)
    expect(validateRun({ ...base, force: true }, 'x')).toMatch(/confirmation/)
    expect(validateRun({ ...base, force: true, forceConfirmed: true }, 'x')).toBeNull()
  })

  it('builds the run body without line_ids and only sends force when confirmed', () => {
    const base = initialForm(config)
    const body = buildRunBody({ ...base, engine: 'openai', cost_cap: '1.5', fallbacks: ['gemini'], force: true, forceConfirmed: true })
    expect(body).toEqual({
      engine: 'openai',
      style_preset: 'natural',
      style_note: '',
      locale: 'en-US',
      force_retranslate: true,
      context_window: 5,
      context_window_ahead: 2,
      batch_size: 20,
      job_cost_cap_usd: 1.5,
      fallback_chain: [{ engine: 'gemini' }],
    })
    expect('line_ids' in body).toBe(false)
    expect(buildRunBody({ ...base, force: true }).force_retranslate).toBe(false)
  })

  it('parses caps and estimate params', () => {
    expect(parseCap('')).toBeUndefined()
    expect(parseCap('abc')).toBeNull()
    expect(parseCap('0')).toBe(0)
    expect(buildEstimateParams({ ...initialForm(config), cost_cap: 'x' })).toBeNull()
    expect(buildEstimateParams({ ...initialForm(config), engine: 'ollama' })).toMatchObject({ engine: 'ollama', force_retranslate: false })
  })

  it('splits one-per-line lists', () => {
    expect(splitLines(' a \n\nb\na\n')).toEqual(['a', 'b'])
  })

  it('gates Reflect and bulk by engine and sends the flags', () => {
    const base = initialForm(config)
    const sup = ['claude', 'gemini', 'deepseek']
    expect(reflectAvailable('deepl')).toBe(false)
    expect(bulkAvailable('ollama', sup)).toBe(false)
    expect(bulkReflectAvailable('deepseek', sup)).toBe(false)
    expect(validateRun({ ...base, engine: 'deepl', reflect: true }, 'x', sup)).toMatch(/Reflect/)
    expect(validateRun({ ...base, engine: 'ollama', bulk: true }, 'x', sup)).toMatch(/Bulk/)
    expect(validateRun({ ...base, engine: 'deepseek', bulk: true, reflect: true }, 'x', sup)).toMatch(/Bulk Reflect/)
    expect(validateRun({ ...base, engine: 'claude', bulk: true, fallbacks: ['gemini'] }, 'x', sup)).toMatch(/Fallback/)
    const ok = { ...base, engine: 'claude', bulk: true, reflect: true }
    expect(validateRun(ok, 'x', sup)).toBeNull()
    expect(buildRunBody(ok)).toMatchObject({ bulk: true, reflect: true })
    expect(buildEstimateParams(ok)).toMatchObject({ bulk: true, reflect: true })
    expect(buildRunBody(base)).not.toHaveProperty('bulk')
  })
})
