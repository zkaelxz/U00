import { describe, expect, it } from 'vitest'

import type { BenchmarkConfig, BenchmarkTranslateChoices } from '../../api/benchmark'
import { configKey, runChangesLabel, runRequestBody } from './benchmarkForm'
import {
  cleanTranslate, translateChangesLabel, translateDefaults, translateNumberProblem, translateValue, withTranslate, withTranslateOn,
} from './translateOptions'

const choices: BenchmarkTranslateChoices = {
  style_presets: [{ key: 'audio_drama', label: 'Audio drama' }, { key: 'novel', label: 'Novel' }],
  series: [{ id: 3, name: 'Saga' }],
  defaults: { context_window: 10, context_window_ahead: 6, batch_size: 30, reflect: false, style_preset: 'audio_drama' },
  batch_size_defaults: { claude: { '': 60, haiku: 30 }, ollama: { '': 30 } },
}
const arm: BenchmarkConfig = { engine: 'claude', model: 'haiku' }

describe('translate like a title', () => {
  it('takes the batch size from the engine and model, else the general default', () => {
    expect(translateDefaults(choices, 'claude', 'haiku').batch_size).toBe(30)
    expect(translateDefaults(choices, 'claude', 'opus').batch_size).toBe(60)
    expect(translateDefaults(choices, 'deepl').batch_size).toBe(30)
  })

  it('shows the default until an option is changed', () => {
    expect(translateValue(arm, choices, 'context_window')).toBe(10)
    expect(translateValue({ ...arm, translate: { context_window: 4 } }, choices, 'context_window')).toBe(4)
  })

  it('keeps only changed options, and an empty set when on but unchanged', () => {
    expect(withTranslateOn(arm, true).translate).toEqual({})
    expect(withTranslateOn(withTranslateOn(arm, true), false)).toEqual(arm)
    const changed = withTranslate(withTranslateOn(arm, true), choices, 'batch_size', 15)
    expect(changed.translate).toEqual({ batch_size: 15 })
    expect(withTranslate(changed, choices, 'batch_size', 30).translate).toEqual({})
  })

  it('drops blanks and non-numbers when cleaning', () => {
    expect(cleanTranslate(null)).toBeUndefined()
    expect(cleanTranslate({ batch_size: NaN, style_preset: ' ', reflect: true, series_id: null })).toEqual({ reflect: true })
  })

  it('checks typed numbers against the server ranges', () => {
    expect(translateNumberProblem('batch_size', 0)).toBe('Use 1 to 200.')
    expect(translateNumberProblem('context_window', 0)).toBeNull()
    expect(translateNumberProblem('context_window_ahead', 2.5)).toBe('Use a whole number.')
  })

  it('labels the changes in form order', () => {
    expect(translateChangesLabel({ context_window: 3, batch_size: 15, reflect: true })).toBe('batch 15, 3 lines before, reflect')
    expect(translateChangesLabel({})).toBe('')
    expect(runChangesLabel({ context_settings: { translate_changes: { style_preset: 'novel' } } })).toBe('novel style')
  })

  it('sends options for a translate arm and tells it apart from a plain one', () => {
    const on = withTranslate(withTranslateOn(arm, true), choices, 'batch_size', 15)
    const body = runRequestBody({ stage: 'translation', configs: [on, withTranslateOn(arm, true), arm], label: '', promptVersion: '', tier: '', setName: '', language: '', limit: '', tag: '', title: '' } as never)
    expect(body.configs).toEqual([
      { engine: 'claude', model: 'haiku', options: { batch_size: 15 } },
      { engine: 'claude', model: 'haiku', options: {} },
      { engine: 'claude', model: 'haiku' },
    ])
    expect(new Set([configKey(on), configKey(withTranslateOn(arm, true)), configKey(arm)]).size).toBe(3)
  })
})
