import { useState } from 'react'

import type { BenchmarkConfig, BenchmarkOptions } from '../../api/benchmark'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { rangeHint } from '../workspace/formatRange'
import {
  TRANSLATE_RANGES, temperatureProblem, translateDefaults, translateNumberProblem, translateValue, withTranslate, withTranslateOn, type TranslateDefaults,
} from './translateOptions'

type NumberKey = 'batch_size' | 'context_window' | 'context_window_ahead'

interface Props {
  options: BenchmarkOptions
  config: BenchmarkConfig
  onChange: (next: BenchmarkConfig) => void
}

/** How a translation arm is run: one case at a time, or a title's cases together the way Translate runs them. */
export function TranslateOptionsFields({ options, config, onChange }: Props) {
  const choices = options.translate_choices
  const on = config.translate !== undefined
  const changed = Object.keys(config.translate ?? {}).length
  // What is typed in a number box until it is a valid number, so clearing the box to retype does not snap back to the default.
  const [draft, setDraft] = useState<Partial<Record<NumberKey, string>>>({})
  const [temperatureDraft, setTemperatureDraft] = useState<string | undefined>(undefined)
  const temperature = translateValue(config, choices, 'temperature')
  const temperatureTyped = temperatureDraft === undefined || temperatureDraft.trim() === '' ? null : Number(temperatureDraft)
  const temperatureError = temperatureTyped !== null && Number.isFinite(temperatureTyped) ? temperatureProblem(temperatureTyped) : null
  const set = (key: keyof TranslateDefaults, value: unknown) => onChange(withTranslate(config, choices, key, value))

  const number = (key: NumberKey, label: string, help: string) => {
    const typed = draft[key]
    const shown = typed ?? String(translateValue(config, choices, key))
    const parsed = typed === undefined || typed.trim() === '' ? null : Number(typed)
    const problem = parsed !== null && Number.isFinite(parsed) ? translateNumberProblem(key, parsed) : null
    const r = TRANSLATE_RANGES[key]
    return (
      <Field label={label} help={`${rangeHint(r.min, r.max, { default: translateDefaults(choices, config.engine, config.model)[key] })} ${help}`} error={problem}>
        <input
          type="number"
          step={1}
          value={shown}
          onChange={(e) => {
            const text = e.target.value
            setDraft({ ...draft, [key]: text })
            if (text.trim() === '') return set(key, undefined)
            const n = Number(text)
            if (Number.isFinite(n) && !translateNumberProblem(key, n)) set(key, n)
          }}
          onBlur={() => setDraft({ ...draft, [key]: undefined })}
        />
      </Field>
    )
  }

  return (
    <details className="bench-real-settings" data-testid="bench-translate-settings">
      <summary>
        Translate like a title
        {on && <span className="muted"> · {changed > 0 ? `${changed} changed` : 'on'}</span>}
      </summary>
      <p className="muted">
        Runs the cases of one title together, in order, with the lines around them as context, the way a title&rsquo;s Translate
        does. Off, every case is translated on its own. A case with no title is always translated alone.
      </p>
      <div className="setting-list">
        <Field label="Translate a title's cases together">
          <Toggle checked={on} onChange={(v) => onChange(withTranslateOn(config, v))} />
        </Field>
      </div>
      {on && (
        <>
          <div className="bench-config-fields">
            {number('batch_size', 'Batch size', 'Lines sent in one request.')}
            {number('context_window', 'Context before', 'Lines before a batch shown as how they were translated.')}
            {number('context_window_ahead', 'Context after', 'Lines after a batch shown as what comes next.')}
            <Field label="Style">
              <select value={translateValue(config, choices, 'style_preset')} onChange={(e) => set('style_preset', e.target.value)}>
                {choices.style_presets.map((p) => (
                  <option key={p.key} value={p.key}>{p.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Series" help="Translates with this series' glossary, learned style and character hints. None: without them.">
              <select
                value={translateValue(config, choices, 'series_id') ?? ''}
                onChange={(e) => set('series_id', e.target.value ? Number(e.target.value) : undefined)}
              >
                <option value="">None</option>
                {choices.series.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="setting-list">
            <Field label="Translation-memory examples">
              <Toggle checked={translateValue(config, choices, 'memory_examples')} onChange={(v) => set('memory_examples', v)} />
            </Field>
          </div>
          {choices.temperature_engines.includes(config.engine) && (
            <div className="bench-config-fields">
              <Field label="Temperature" error={temperatureError}>
                <input
                  type="number"
                  step={0.1}
                  value={temperatureDraft ?? (temperature === null ? '' : String(temperature))}
                  onChange={(e) => {
                    const text = e.target.value
                    setTemperatureDraft(text)
                    if (text.trim() === '') return set('temperature', undefined)
                    const n = Number(text)
                    if (Number.isFinite(n) && !temperatureProblem(n)) set('temperature', n)
                  }}
                  onBlur={() => setTemperatureDraft(undefined)}
                />
              </Field>
            </div>
          )}
          <div className="setting-list">
            <Field label="Reflect" help="Three passes per batch: translate, critique, rewrite. Slower and about three times the cost.">
              <Toggle checked={translateValue(config, choices, 'reflect')} onChange={(v) => set('reflect', v)} />
            </Field>
          </div>
          {changed > 0 && (
            <div className="actions">
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { setDraft({}); onChange({ ...config, translate: {} }) }}>
                Back to a title&rsquo;s defaults
              </button>
            </div>
          )}
        </>
      )}
    </details>
  )
}
