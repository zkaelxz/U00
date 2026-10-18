// A translation arm's "translate like a title" options: pure helpers for the Run card's form. An arm with no
// `translate` field is run one case at a time as before; one with it (even empty) runs a title's cases together,
// in order, and holds only the fields changed from what a title's Translate starts with.
import type { BenchmarkConfig, BenchmarkTranslateChoices, BenchmarkTranslateOptions } from '../../api/benchmark'

export const TRANSLATE_OPTION_KEYS = [
  'batch_size', 'context_window', 'context_window_ahead', 'reflect', 'style_preset', 'series_id',
] as const

type NumberKey = 'batch_size' | 'context_window' | 'context_window_ahead'

/** Lines per request and lines of context; the server checks the same ranges. */
export const TRANSLATE_RANGES: Record<NumberKey, { min: number; max: number }> = {
  batch_size: { min: 1, max: 200 },
  context_window: { min: 0, max: 100 },
  context_window_ahead: { min: 0, max: 100 },
}

/** The options a person changed, in the order the form shows them; undefined when the arm is not a translate-like-a-title arm. */
export function cleanTranslate(options: unknown): BenchmarkTranslateOptions | undefined {
  if (!options || typeof options !== 'object') return undefined
  const given = options as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const key of TRANSLATE_OPTION_KEYS) {
    const v = given[key]
    if (v === undefined || v === null || (typeof v === 'string' && !v.trim()) || (typeof v === 'number' && !Number.isFinite(v))) continue
    out[key] = typeof v === 'string' ? v.trim() : v
  }
  return out as BenchmarkTranslateOptions
}

export interface TranslateDefaults {
  batch_size: number
  context_window: number
  context_window_ahead: number
  reflect: boolean
  style_preset: string
  series_id: number | null
}

/** What a title's Translate starts with for this engine and model (the batch size follows the model). */
export function translateDefaults(choices: BenchmarkTranslateChoices, engine: string, model?: string): TranslateDefaults {
  const perEngine = choices.batch_size_defaults[engine] ?? {}
  return {
    batch_size: perEngine[model ?? ''] ?? perEngine[''] ?? choices.defaults.batch_size,
    context_window: choices.defaults.context_window,
    context_window_ahead: choices.defaults.context_window_ahead,
    reflect: choices.defaults.reflect,
    style_preset: choices.defaults.style_preset,
    series_id: null,
  }
}

/** An option as the form shows it: its changed value, else the default. */
export function translateValue<K extends keyof TranslateDefaults>(
  config: BenchmarkConfig, choices: BenchmarkTranslateChoices, key: K,
): TranslateDefaults[K] {
  const v = config.translate?.[key]
  return (v === undefined ? translateDefaults(choices, config.engine, config.model)[key] : v) as TranslateDefaults[K]
}

/** `config` with one option set; setting it back to the default drops it. */
export function withTranslate(
  config: BenchmarkConfig, choices: BenchmarkTranslateChoices, key: keyof TranslateDefaults, value: unknown,
): BenchmarkConfig {
  const next: Record<string, unknown> = { ...(config.translate ?? {}) }
  if (value === undefined || value === null || value === translateDefaults(choices, config.engine, config.model)[key]) delete next[key]
  else next[key] = value
  return { ...config, translate: cleanTranslate(next) }
}

/** Turns "translate like a title" on (nothing changed yet) or off (the arm is run one case at a time). */
export function withTranslateOn(config: BenchmarkConfig, on: boolean): BenchmarkConfig {
  const { translate: _dropped, ...rest } = config
  return on ? { ...rest, translate: config.translate ?? {} } : rest
}

/** The error for a typed number, or null when it is acceptable. */
export function translateNumberProblem(key: NumberKey, value: number): string | null {
  const r = TRANSLATE_RANGES[key]
  if (value < r.min || value > r.max) return `Use ${r.min} to ${r.max}.`
  return Number.isInteger(value) ? null : 'Use a whole number.'
}

const CHANGE_LABELS: Record<string, (v: unknown) => string> = {
  batch_size: (v) => `batch ${v}`,
  context_window: (v) => `${v} lines before`,
  context_window_ahead: (v) => `${v} lines after`,
  reflect: (v) => (v ? 'reflect' : 'no reflect'),
  style_preset: (v) => `${String(v).replace(/_/g, ' ')} style`,
  series_id: () => 'series glossary',
}

/** "batch 15, 3 lines before" for the options a run changed from a title's defaults; '' for none. */
export function translateChangesLabel(changes: unknown): string {
  if (!changes || typeof changes !== 'object') return ''
  return TRANSLATE_OPTION_KEYS.filter((k) => k in (changes as object))
    .map((k) => CHANGE_LABELS[k]((changes as Record<string, unknown>)[k]))
    .join(', ')
}
