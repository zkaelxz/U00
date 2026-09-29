import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TranslateRunConfig, WorkflowTierApplied } from '../../types/translateStage'
import {
  applyTierToForm,
  buildEstimateParams,
  buildPresetBody,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  initialForm,
  loadPresetStart,
  monthSpendText,
  parseCap,
  reflectAvailable,
  savePresetStart,
  splitLines,
  validatePresetName,
  validateRun,
  withSavedEngine,
} from './translateForm'

const config = {
  default_style_preset: 'natural',
  style_presets: [{ key: 'natural', label: 'Natural' }, { key: 'wuxia', label: 'Wuxia' }],
  locales: ['en-GB', 'en-US'],
  defaults: { context_window: 5, context_window_ahead: 2, batch_size: 20 },
} as TranslateRunConfig

describe('translate form', () => {
  it('starts from the config defaults', () => {
    const f = initialForm(config)
    expect(f).toMatchObject({ locale: 'en-US', batch_size: '20', context_window: '5', style_preset: 'natural' })
    expect(validateRun(f, 'ollama')).toBeNull()
  })

  it('starts from the Settings default locale and style note when given', () => {
    const f = initialForm({ ...config, default_locale: 'en-GB', default_style_note: 'Terse.' })
    expect(f).toMatchObject({ locale: 'en-GB', style_note: 'Terse.' })
    // An unknown default locale falls back to en-US; a preset's locale still wins.
    expect(initialForm({ ...config, default_locale: 'fr-FR' }).locale).toBe('en-US')
    expect(initialForm({ ...config, default_locale: 'en-GB' }, { locale: 'en-US' }).locale).toBe('en-US')
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
      default_female_pronouns: false,
      include_genre_notes: true,
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

  it('describes month spend without implying a zero cap was reached', () => {
    expect(monthSpendText(1.5, 10)).toBe('Spend this month: $1.50 of $10.00.')
    // No cap set: the API still reports the real month spend.
    expect(monthSpendText(4.25, 0)).toBe('Spent this month: $4.25 (no monthly cap).')
    expect(monthSpendText(4.25, -1)).toBe('Spent this month: $4.25 (no monthly cap).')
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

function memory(): Storage {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  } as Storage
}

describe('preset start values', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('starts the form from the preset style and locale when the server offers them', () => {
    const f = initialForm(config, { style_preset: 'wuxia', locale: 'en-GB' })
    expect(f).toMatchObject({ style_preset: 'wuxia', locale: 'en-GB' })
  })

  it('falls back to the defaults for values the server does not offer', () => {
    const f = initialForm(config, { style_preset: 'gone', locale: 'fr-FR' })
    expect(f).toMatchObject({ style_preset: 'natural', locale: 'en-US' })
  })

  it('remembers per drama, keeps only style and locale, and clears on an empty preset', () => {
    vi.stubGlobal('localStorage', memory())
    savePresetStart(3, { style_preset: 'wuxia', locale: 'en-GB' })
    expect(loadPresetStart(3)).toEqual({ style_preset: 'wuxia', locale: 'en-GB' })
    expect(loadPresetStart(4)).toEqual({})
    savePresetStart(5, { style_preset: null, locale: 'en-GB' })
    expect(loadPresetStart(5)).toEqual({ locale: 'en-GB' })
    savePresetStart(3, null)
    expect(loadPresetStart(3)).toEqual({})
  })

  it('remembers the pronoun, genre and model values from preset_defaults', () => {
    vi.stubGlobal('localStorage', memory())
    savePresetStart(7, {
      style_preset: null, locale: null, default_female_pronouns: true,
      include_genre_notes: false, engine_model: 'gemini-pro',
    })
    expect(loadPresetStart(7)).toEqual({
      default_female_pronouns: true, include_genre_notes: false, engine_model: 'gemini-pro',
    })
    savePresetStart(8, { default_female_pronouns: false, include_genre_notes: true, engine_model: null })
    expect(loadPresetStart(8)).toEqual({ default_female_pronouns: false, include_genre_notes: true })
  })

  it('starts the toggles from the preset (defaults: she/her off, genre notes on) and sends them', () => {
    const plain = initialForm(config)
    expect(plain).toMatchObject({ female_pronouns: false, genre_notes: true })
    expect(buildRunBody(plain)).toMatchObject({ default_female_pronouns: false, include_genre_notes: true })
    const f = initialForm(config, { default_female_pronouns: true, include_genre_notes: false })
    expect(f).toMatchObject({ female_pronouns: true, genre_notes: false })
    expect(buildRunBody(f)).toMatchObject({ default_female_pronouns: true, include_genre_notes: false })
  })

  it('prefills the preset model only when the drama engine offers it', () => {
    const withEngines = {
      ...config,
      translation_engine: 'gemini',
      engines: [{ name: 'gemini', models: ['gemini-flash', 'gemini-pro'] }, { name: 'claude', models: ['c1'] }],
    } as unknown as TranslateRunConfig
    const f = initialForm(withEngines, { engine_model: 'gemini-pro' })
    expect(f).toMatchObject({ engine: '', model: 'gemini-pro' })
    expect(buildRunBody(f)).toMatchObject({ model: 'gemini-pro' })
    expect(buildEstimateParams(f)).toMatchObject({ model: 'gemini-pro' })
    expect(initialForm(withEngines, { engine_model: 'c1' }).model).toBe('')
    expect(initialForm({ ...withEngines, translation_engine: 'claude' }, { engine_model: 'gemini-pro' }).model).toBe('')
    expect(initialForm(config, { engine_model: 'gemini-pro' }).model).toBe('')
  })

  it('ignores corrupt data and survives throwing storage', () => {
    vi.stubGlobal('localStorage', { getItem: () => '{"style_preset":5,"locale":"en-GB"}' })
    expect(loadPresetStart(1)).toEqual({ locale: 'en-GB' })
    const boom = () => {
      throw new Error('blocked')
    }
    vi.stubGlobal('localStorage', { getItem: boom, setItem: boom, removeItem: boom })
    expect(loadPresetStart(1)).toEqual({})
    expect(() => savePresetStart(1, { locale: 'en-GB' })).not.toThrow()
  })
})

describe('workflow tiers (X02) and save as preset (X22)', () => {
  const withEngines = {
    ...config,
    translation_engine: 'deepl',
    engines: [
      { name: 'claude', models: ['claude-sonnet-5', 'claude-opus-4-8'] },
      { name: 'deepseek', models: null },
      { name: 'deepl', models: null },
    ],
    bulk_supported_engines: ['claude', 'deepseek'],
  } as unknown as TranslateRunConfig
  const release: WorkflowTierApplied = {
    drama_id: 1, tier: 'release', label: 'Release', translation_engine: 'claude',
    engine_model: 'claude-opus-4-8', reflect: true, auto_qc: true,
  }
  const draft: WorkflowTierApplied = {
    drama_id: 1, tier: 'draft', label: 'Draft', translation_engine: 'deepseek',
    engine_model: null, reflect: false, auto_qc: false,
  }

  it('fills engine, model and Reflect and leaves the rest alone', () => {
    const base = { ...initialForm(withEngines), style_note: 'keep', locale: 'en-GB', genre_notes: false }
    const f = applyTierToForm(base, release, withEngines)
    expect(f).toMatchObject({ engine: 'claude', model: 'claude-opus-4-8', reflect: true })
    expect(f).toMatchObject({ style_note: 'keep', locale: 'en-GB', genre_notes: false, force: false })
    const d = applyTierToForm(f, draft, withEngines)
    expect(d).toMatchObject({ engine: 'deepseek', model: '', reflect: false })
  })

  it('drops an unlisted model and a bulk choice that no longer fits', () => {
    const noOpus = { ...withEngines, engines: [{ name: 'claude', models: ['claude-sonnet-5'] }] } as unknown as TranslateRunConfig
    expect(applyTierToForm(initialForm(noOpus), release, noOpus).model).toBe('')
    const bulky = { ...initialForm(withEngines), engine: 'claude', bulk: true }
    // Release: Reflect + bulk needs a batch API; claude has one.
    expect(applyTierToForm(bulky, release, withEngines).bulk).toBe(true)
    // Draft on DeepSeek: plain bulk (off-peak) still fits.
    expect(applyTierToForm(bulky, draft, withEngines).bulk).toBe(true)
    const noBulk = { ...withEngines, bulk_supported_engines: [] } as unknown as TranslateRunConfig
    expect(applyTierToForm(bulky, release, noBulk).bulk).toBe(false)
  })

  it('after a tier, Default means the new engine for validation and presets', () => {
    // The drama was on deepl (translation-only: no Reflect); Release saves claude.
    const c = withSavedEngine(withEngines, release)
    expect(c.translation_engine).toBe('claude')
    expect(withSavedEngine(c, release)).toBe(c)
    const f = { ...applyTierToForm(initialForm(withEngines), release, withEngines), engine: '', model: '' }
    expect(validateRun(f, withEngines.translation_engine)).toMatch(/cannot run Reflect/)
    expect(validateRun(f, c.translation_engine)).toBeNull()
    expect(buildPresetBody(f, c.translation_engine, 'Mine').translation_engine).toBe('claude')
    expect(buildRunBody(f).engine).toBeUndefined()   // server uses the drama's (new) engine
  })

  it('validates the preset name', () => {
    expect(validatePresetName('   ')).toMatch(/name/)
    expect(validatePresetName('x'.repeat(101))).toMatch(/100/)
    expect(validatePresetName(' Mine ')).toBeNull()
  })

  it('builds the preset body from the form', () => {
    const f = { ...initialForm(withEngines), style_preset: 'wuxia', locale: 'en-GB', female_pronouns: true, genre_notes: false }
    expect(buildPresetBody(f, 'deepl', ' Mine ')).toEqual({
      name: 'Mine', translation_engine: 'deepl', engine_model: null, style_preset: 'wuxia',
      locale: 'en-GB', default_female_pronouns: true, include_genre_notes: false,
    })
    const g = { ...f, engine: 'claude', model: 'claude-sonnet-5' }
    expect(buildPresetBody(g, 'deepl', 'Mine', true)).toMatchObject({
      translation_engine: 'claude', engine_model: 'claude-sonnet-5', overwrite: true,
    })
  })
})
