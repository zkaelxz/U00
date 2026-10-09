import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TranslatePresetApplied, TranslateRunConfig, WorkflowTierApplied } from '../../types/translateStage'
import {
  applyPresetToForm,
  applyTierToForm,
  buildEstimateParams,
  buildPresetBody,
  buildRunBody,
  bulkAvailable,
  bulkReflectAvailable,
  FALLBACK_KIND_MESSAGE,
  failedBatches,
  fallbackKindMismatch,
  fallbackOptions,
  initialForm,
  isTranslationOnly,
  lineRanges,
  loadPresetStart,
  MAX_FALLBACKS,
  monthSpendText,
  cloudModelNotice,
  ollamaWarning,
  parseCap,
  reflectAvailable,
  sameEngineKind,
  thinkingApplies,
  thinkingEngines,
  thinkingHelp,
  restoreRunOptions,
  saveRunOptions,
  savePresetStart,
  splitLines,
  translateButtonLabel,
  styleGuidance,
  validatePresetName,
  TRANSLATION_ONLY,
  validateRun,
  withPresetEngine,
  withSavedEngine,
} from './translateForm'

const config = {
  default_style_preset: 'natural',
  style_presets: [{ key: 'natural', label: 'Natural' }, { key: 'wuxia', label: 'Wuxia' }],
  locales: ['en-GB', 'en-US'],
  defaults: { context_window: 5, context_window_ahead: 2, batch_size: 20 },
} as TranslateRunConfig

// No translation-only engine is offered now; the guards still apply to one.
beforeEach(() => {
  TRANSLATION_ONLY.push('fake_mt')
})
afterEach(() => {
  TRANSLATION_ONLY.length = 0
})

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
    expect(MAX_FALLBACKS).toBe(2)
    expect(validateRun({ ...base, fallbacks: ['a', 'b', 'c'] }, 'x')).toMatch(/At most 2/)
    expect(validateRun({ ...base, fallbacks: ['a', 'b'] }, 'x')).toBeNull()
    expect(validateRun({ ...base, fallbacks: [''] }, 'x')).toMatch(/every fallback/)
    expect(validateRun({ ...base, fallbacks: ['x'] }, 'x')).toMatch(/twice/)
    expect(validateRun({ ...base, engine: 'a', fallbacks: ['b', 'b'] }, 'x')).toMatch(/twice/)
    expect(validateRun({ ...base, force: true }, 'x')).toMatch(/confirmation/)
    expect(validateRun({ ...base, force: true, forceConfirmed: true }, 'x')).toBeNull()
  })

  it('offers only fallback engines of the main engine\'s kind, not already used', () => {
    const all = ['claude', 'gemini', 'openai', 'fake_mt']
    expect(isTranslationOnly('fake_mt')).toBe(true)
    expect(isTranslationOnly('claude')).toBe(false)
    expect(sameEngineKind('claude', 'openai')).toBe(true)
    expect(sameEngineKind('claude', 'fake_mt')).toBe(false)
    // AI main engine: AI engines only, minus the main engine.
    expect(fallbackOptions(all, 'claude', [''], 0)).toEqual(['gemini', 'openai'])
    // A slot never offers an engine chosen in another slot, but keeps its own.
    expect(fallbackOptions(all, 'claude', ['gemini', 'openai'], 1)).toEqual(['openai'])
    expect(fallbackOptions(all, 'claude', ['gemini', 'openai'], 0)).toEqual(['gemini'])
    // Translation-only main engine: translation-only engines only.
    expect(fallbackOptions(all, 'fake_mt', [], -1)).toEqual([])
  })

  it('flags a fallback of a different kind from the main engine', () => {
    const base = initialForm(config)
    expect(fallbackKindMismatch('claude', ['gemini', ''])).toBe(false)
    expect(fallbackKindMismatch('claude', ['fake_mt'])).toBe(true)
    expect(validateRun({ ...base, engine: 'claude', fallbacks: ['gemini'] }, 'x')).toBeNull()
    // The main engine switched kind after the fallback was picked.
    expect(validateRun({ ...base, engine: 'fake_mt', fallbacks: ['gemini'] }, 'x')).toBe(FALLBACK_KIND_MESSAGE)
    // With no engine chosen, the drama's default engine decides the kind.
    expect(validateRun({ ...base, fallbacks: ['fake_mt'] }, 'claude')).toBe(FALLBACK_KIND_MESSAGE)
    // Reflect/Bulk are refused first, with their own reason.
    expect(validateRun({ ...base, engine: 'claude', reflect: true, fallbacks: ['fake_mt'] }, 'x')).toMatch(/normal run/)
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
      thinking: false,
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
    expect(reflectAvailable('fake_mt')).toBe(false)
    expect(bulkAvailable('ollama', sup)).toBe(false)
    expect(bulkReflectAvailable('deepseek', sup)).toBe(false)
    expect(validateRun({ ...base, engine: 'fake_mt', reflect: true }, 'x', sup)).toMatch(/Reflect/)
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

  it("starts the toggles from the title's saved choice before a preset or the defaults", () => {
    const saved = { ...config, default_female_pronouns: true, include_genre_notes: false } as TranslateRunConfig
    expect(initialForm(saved)).toMatchObject({ female_pronouns: true, genre_notes: false })
    expect(initialForm(saved, { default_female_pronouns: false, include_genre_notes: true })).toMatchObject({
      female_pronouns: true,
      genre_notes: false,
    })
    const half = { ...config, default_female_pronouns: false, include_genre_notes: null } as TranslateRunConfig
    expect(initialForm(half, { default_female_pronouns: true, include_genre_notes: false })).toMatchObject({
      female_pronouns: false,
      genre_notes: false,
    })
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
    translation_engine: 'fake_mt',
    engines: [
      { name: 'claude', models: ['claude-sonnet-5-5', 'claude-opus-5-5'] },
      { name: 'deepseek', models: null },
      { name: 'fake_mt', models: null },
    ],
    bulk_supported_engines: ['claude', 'deepseek'],
  } as unknown as TranslateRunConfig
  const release: WorkflowTierApplied = {
    drama_id: 1, tier: 'release', label: 'Release', translation_engine: 'claude',
    engine_model: 'claude-opus-5-5', reflect: true, auto_qc: true,
  }
  const draft: WorkflowTierApplied = {
    drama_id: 1, tier: 'draft', label: 'Draft', translation_engine: 'deepseek',
    engine_model: null, reflect: false, auto_qc: false,
  }

  it('fills engine, model and Reflect and leaves the rest alone', () => {
    const base = { ...initialForm(withEngines), style_note: 'keep', locale: 'en-GB', genre_notes: false }
    const f = applyTierToForm(base, release, withEngines)
    expect(f).toMatchObject({ engine: 'claude', model: 'claude-opus-5-5', reflect: true })
    expect(f).toMatchObject({ style_note: 'keep', locale: 'en-GB', genre_notes: false, force: false })
    const d = applyTierToForm(f, draft, withEngines)
    expect(d).toMatchObject({ engine: 'deepseek', model: '', reflect: false })
  })

  it('drops an unlisted model and a bulk choice that no longer fits', () => {
    const noOpus = { ...withEngines, engines: [{ name: 'claude', models: ['claude-sonnet-5-5'] }] } as unknown as TranslateRunConfig
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
    // The drama was on fake_mt (translation-only: no Reflect); Release saves claude.
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
    expect(buildPresetBody(f, 'fake_mt', ' Mine ')).toEqual({
      name: 'Mine', translation_engine: 'fake_mt', engine_model: null, style_preset: 'wuxia',
      locale: 'en-GB', default_female_pronouns: true, include_genre_notes: false,
    })
    const g = { ...f, engine: 'claude', model: 'claude-sonnet-5' }
    expect(buildPresetBody(g, 'fake_mt', 'Mine', true)).toMatchObject({
      translation_engine: 'claude', engine_model: 'claude-sonnet-5', overwrite: true,
    })
  })
})

describe('apply a saved preset (parity X03/X04)', () => {
  const c = {
    ...config,
    translation_engine: 'fake_mt',
    style_presets: [{ key: 'natural', label: 'Natural', guidance: 'Sound natural.' }, { key: 'wuxia', label: 'Wuxia' }],
    engines: [
      { name: 'claude', models: ['claude-a', 'claude-b'] },
      { name: 'fake_mt', models: [] },
    ],
    bulk_supported_engines: ['claude'],
  } as unknown as TranslateRunConfig
  const preset = (o: Partial<TranslatePresetApplied> = {}): TranslatePresetApplied => ({
    drama_id: 1, preset_id: 2, name: 'Mine', translation_engine: 'claude', engine_model: 'claude-b',
    style_preset: 'wuxia', locale: 'en-GB', default_female_pronouns: true, include_genre_notes: false, ...o,
  })

  it('fills engine, model, style, locale and toggles', () => {
    const f = applyPresetToForm(initialForm(c), preset(), c)
    expect(f).toMatchObject({
      engine: 'claude', model: 'claude-b', style_preset: 'wuxia', locale: 'en-GB',
      female_pronouns: true, genre_notes: false,
    })
  })

  it('skips values the server no longer offers and keeps the engine when the preset has none', () => {
    const base = { ...initialForm(c), model: 'kept' }
    const f = applyPresetToForm(base, preset({ translation_engine: null, engine_model: null, style_preset: 'gone', locale: 'xx' }), c)
    expect(f).toMatchObject({ engine: '', model: 'kept', style_preset: 'natural', locale: 'en-US' })
    const g = applyPresetToForm(initialForm(c), preset({ engine_model: 'claude-z' }), c)
    expect(g.model).toBe('')
  })

  it('drops Reflect/Bulk that the new engine cannot run', () => {
    const base = { ...initialForm(c), engine: 'claude', reflect: true, bulk: true }
    const f = applyPresetToForm(base, preset({ translation_engine: 'fake_mt', engine_model: null }), c)
    expect(f).toMatchObject({ engine: 'fake_mt', reflect: false, bulk: false })
  })

  it('moves the config default engine only when the preset has one', () => {
    expect(withPresetEngine(c, preset()).translation_engine).toBe('claude')
    expect(withPresetEngine(c, preset({ translation_engine: null }))).toBe(c)
  })

  it('reads the style guidance text', () => {
    expect(styleGuidance(c, 'natural')).toBe('Sound natural.')
    expect(styleGuidance(c, 'wuxia')).toBe('')
  })
})

describe('failed batches notice (X01)', () => {
  it('summarises batches, 1-based lines and distinct reasons', () => {
    const r = failedBatches([
      { batch_index: 0, lines: [0, 1, 2], error: 'timeout' },
      { batch_index: 3, lines: [8, 2], error: 'timeout' },
      { batch_index: 4, lines: [11], error: 'blocked' },
    ])
    expect(r).toEqual({ batches: 3, lineNumbers: [1, 2, 3, 9, 12], reasons: ['timeout', 'blocked'] })
    expect(lineRanges(r!.lineNumbers)).toBe('1-3, 9, 12')
  })
  it('is null for nothing and tolerates a malformed record', () => {
    expect(failedBatches(null)).toBeNull()
    expect(failedBatches([])).toBeNull()
    expect(failedBatches({ lines: [1] })).toBeNull()
    expect(failedBatches([null, 'x', { lines: ['a', -1, 1.5, 4] }])).toEqual({ batches: 3, lineNumbers: [5], reasons: [] })
    expect(lineRanges([])).toBe('')
  })
})

describe('Ollama reachability warning (X24)', () => {
  it('warns only when the engine in use is Ollama and the server got no answer', () => {
    expect(ollamaWarning('ollama', false)).toBe(true)
    expect(ollamaWarning('ollama', true)).toBe(false)
  })

  it('stays quiet when reachability was not checked (saved engine is not Ollama)', () => {
    expect(ollamaWarning('ollama', null)).toBe(false)
    expect(ollamaWarning('ollama', undefined)).toBe(false)
  })

  it('stays quiet for other engines, even with a stale false', () => {
    expect(ollamaWarning('claude', false)).toBe(false)
    expect(ollamaWarning('', false)).toBe(false)
  })
})

describe('think harder on tricky text', () => {
  it('is off unless the title remembers it, and is sent explicitly either way', () => {
    expect(initialForm(config).thinking).toBe(false)
    const remembered = initialForm({ ...config, title_thinking: true })
    expect(remembered.thinking).toBe(true)
    expect(buildRunBody(remembered).thinking).toBe(true)
    expect(buildRunBody({ ...remembered, thinking: false }).thinking).toBe(false)
    expect(buildEstimateParams(remembered)).toMatchObject({ thinking: true })
  })

  it('applies only to engines with a request switch, and never to Reflect', () => {
    expect(thinkingApplies('deepseek', false)).toBe(true)
    expect(thinkingApplies('ollama', false)).toBe(true)
    expect(thinkingApplies('claude', false)).toBe(false)
    expect(thinkingApplies('deepseek', true)).toBe(false)
    expect(thinkingApplies('claude', false, ['claude'])).toBe(true)
  })

  it('applies when any engine in the fallback chain has a switch', () => {
    expect(thinkingApplies('claude', false, undefined, ['deepseek'])).toBe(true)
    expect(thinkingApplies('claude', false, undefined, ['gemini', ''])).toBe(false)
    expect(thinkingApplies('claude', true, undefined, ['deepseek'])).toBe(false)
    expect(thinkingEngines('deepseek', ['claude', 'ollama'], false)).toEqual(['deepseek', 'ollama'])
  })

  it('names the engines it applies to, or the chain that has none', () => {
    expect(thinkingHelp('claude', false, undefined, ['deepseek'])).toMatch(/applies to deepseek, not to the other engines/)
    expect(thinkingHelp('deepseek', false)).not.toMatch(/applies to/)
    expect(thinkingHelp('claude', false, undefined, ['gemini'])).toMatch(/^claude and gemini have no thinking switch/)
  })

  it('says plainly what it costs, and plainly when it does nothing', () => {
    expect(thinkingHelp('deepseek', false)).toMatch(/Off by default/)
    expect(thinkingHelp('deepseek', false)).toMatch(/lower bound/)
    expect(thinkingHelp('claude', false, undefined, ['deepseek'])).not.toMatch(/lower bound/)
    expect(thinkingHelp('claude', false, undefined, ['deepseek'])).toMatch(/does not include it/)
    expect(thinkingHelp('claude', false)).toBe(
      'claude has no thinking switch, so this does nothing for this run; it runs as it always has. Thinking can be switched for DeepSeek and Ollama.',
    )
    expect(thinkingHelp('deepseek', true)).toMatch(/^Reflect mode has no thinking switch/)
  })
})

describe('cloud model notice', () => {
  const engine = { cloud_models: ['gemma4:31b-cloud'] }
  it('warns in plain words only for a cloud model', () => {
    expect(cloudModelNotice(engine, 'gemma4:31b-cloud')).toMatch(/off this PC/)
    expect(cloudModelNotice(engine, 'gemma4:31b-cloud')).toMatch(/limits/)
  })
  it('follows the server flag for a cloud tag that is not built in', () => {
    expect(cloudModelNotice({ cloud_models: ['gpt-oss:120b-cloud'] }, 'gpt-oss:120b-cloud')).toMatch(/off this PC/)
  })
  it('stays quiet for local, empty and unknown engines', () => {
    expect(cloudModelNotice(engine, 'gemma4:12b')).toBeNull()
    expect(cloudModelNotice(engine, '')).toBeNull()
    expect(cloudModelNotice(undefined, 'gemma4:31b-cloud')).toBeNull()
  })
})

describe('remembered run options', () => {
  const memory = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }
  const cfg = {
    ...config,
    translation_engine: 'deepseek',
    bulk_supported_engines: ['claude'],
    engines: [{ name: 'deepseek', models: ['d1'] }, { name: 'claude', models: ['c1', 'c2'] }],
  } as unknown as TranslateRunConfig
  const save = (id: number, patch: object, extra: object = {}) =>
    saveRunOptions(id, { form: { ...initialForm(cfg), ...patch }, baseEngine: 'deepseek', reviewFirst: false, tier: '', ...extra })
  const restore = (id: number, c = cfg) => restoreRunOptions(id, initialForm(c), c)

  beforeEach(() => void vi.stubGlobal('localStorage', memory()))

  it('saves and restores the choices, including the glossary toggle and tier', () => {
    save(1, {
      engine: 'claude', model: 'c2', style_preset: 'wuxia', locale: 'en-GB', style_note: 'keep puns',
      batch_size: '30', context_window: '7', context_window_ahead: '3', cost_cap: '1.5', thinking: true,
    }, { reviewFirst: true, tier: 'release' })
    const r = restore(1)
    expect(r.form).toMatchObject({
      engine: 'claude', model: 'c2', style_preset: 'wuxia', locale: 'en-GB', style_note: 'keep puns',
      batch_size: '30', context_window: '7', context_window_ahead: '3', cost_cap: '1.5', thinking: true,
    })
    expect(r.reviewFirst).toBe(true)
    expect(r.tier).toBe('release')
  })

  it('never restores a pending re-translate or stores anything but plain choices', () => {
    save(1, { force: true, forceConfirmed: true })
    expect(restore(1).form).toMatchObject({ force: false, forceConfirmed: false })
    const raw = JSON.stringify(Object.entries(localStorage as unknown as object))
    expect(raw).not.toMatch(/key|secret|force/i)
  })

  it('falls back to the defaults for values that are no longer valid', () => {
    save(1, {
      engine: 'gone', style_preset: 'gone', locale: 'fr-FR', batch_size: 'abc', cost_cap: '-3',
    })
    expect(restore(1).form).toEqual(initialForm(cfg))
    save(2, { engine: 'claude', model: 'nope', fallbacks: ['claude', 'ghost'] })
    expect(restore(2).form).toMatchObject({ engine: 'claude', model: '', fallbacks: [] })
    save(3, { reflect: true, bulk: true, engine: 'claude' })
    expect(restore(3).form).toMatchObject({ reflect: true, bulk: false })
  })

  it('clamps numbers to the field range', () => {
    save(1, { batch_size: '500', context_window: '0', context_window_ahead: '101' })
    expect(restore(1).form).toMatchObject({ batch_size: '60', context_window: '0', context_window_ahead: '100' })
  })

  it("lets the title's saved engine win when it changed since", () => {
    save(1, { engine: 'claude', model: 'c1', style_preset: 'wuxia' })
    const moved = { ...cfg, translation_engine: 'claude' } as TranslateRunConfig
    expect(restore(1, moved).form).toMatchObject({ engine: '', model: '', style_preset: 'wuxia' })
  })

  it('keeps each drama separate', () => {
    save(1, { style_preset: 'wuxia' })
    save(2, { locale: 'en-GB' })
    expect(restore(1).form).toMatchObject({ style_preset: 'wuxia', locale: 'en-US' })
    expect(restore(2).form).toMatchObject({ style_preset: 'natural', locale: 'en-GB' })
    expect(restore(3).form).toEqual(initialForm(cfg))
  })

  it('ignores corrupt data and survives throwing storage', () => {
    localStorage.setItem('baihe.translateRun.v1.1', '{nope')
    expect(restore(1).form).toEqual(initialForm(cfg))
    const boom = () => {
      throw new Error('blocked')
    }
    vi.stubGlobal('localStorage', { getItem: boom, setItem: boom, removeItem: boom })
    expect(restore(1).form).toEqual(initialForm(cfg))
    expect(() => save(1, {})).not.toThrow()
  })
})

describe('translate button label', () => {
  it('says what the button does', () => {
    expect(translateButtonLabel(false, 1)).toBe('Translate 1 line')
    expect(translateButtonLabel(false, 4)).toBe('Translate 4 lines')
    expect(translateButtonLabel(true, 4)).toBe('Scan glossary, then translate')
  })
})
