// Pure form logic for the profile editor in Library tools (no React). A
// profile is a saved preset widened with the Translate run options and the
// transcribe options (services/profile_service.py). Blank = not set, so the
// title keeps its own value for that option. The server checks every value.

import type { LibraryPreset, ProfileField, ProfileOptions } from '../../types/library'
import type { TranslatePresetUpdateBody } from '../../types/translateStage'

export const MAX_FALLBACKS = 2

export interface ProfileForm {
  name: string
  engine: string
  model: string // '' = engine default
  style_preset: string
  genre_notes: boolean
  female_pronouns: boolean
  style_note: string
  reflect: boolean
  bulk: boolean
  thinking: boolean
  batch_size: string
  context_window: string
  context_window_ahead: string
  cost_cap: string
  fallbacks: string[]
  // Transcribe options and translate-by-sentence, as the text of the control: '' unset,
  // 'true' / 'false' for a switch, else the chosen or typed value.
  title: Record<string, string>
}

export const emptyProfile = (engine: string, style: string): ProfileForm => ({
  name: '', engine, model: '', style_preset: style, genre_notes: true, female_pronouns: false,
  style_note: '', reflect: false, bulk: false, thinking: false, batch_size: '', context_window: '',
  context_window_ahead: '', cost_cap: '', fallbacks: [], title: {},
})

const RUN_KEYS = new Set([
  'style_note', 'batch_size', 'context_window', 'context_window_ahead', 'cost_cap',
  'fallback_engines', 'reflect', 'bulk', 'thinking',
])

export function fromPreset(p: LibraryPreset, defaultStyle: string): ProfileForm {
  const o: ProfileOptions = p.options ?? {}
  const f = emptyProfile(p.translation_engine ?? '', p.style_preset ?? defaultStyle)
  f.name = p.name
  f.model = p.engine_model ?? ''
  f.genre_notes = p.include_genre_notes == null ? true : !!p.include_genre_notes
  f.female_pronouns = !!p.default_female_pronouns
  f.style_note = o.style_note ?? ''
  f.reflect = o.reflect === true
  f.bulk = o.bulk === true
  f.thinking = o.thinking === true
  f.batch_size = o.batch_size === undefined ? '' : String(o.batch_size)
  f.context_window = o.context_window === undefined ? '' : String(o.context_window)
  f.context_window_ahead = o.context_window_ahead === undefined ? '' : String(o.context_window_ahead)
  f.cost_cap = o.cost_cap === undefined ? '' : String(o.cost_cap)
  f.fallbacks = o.fallback_engines ?? []
  for (const [k, v] of Object.entries(o)) {
    if (!RUN_KEYS.has(k) && v !== undefined) f.title[k] = String(v)
  }
  return f
}

const whole = (raw: string, lo: number, hi: number) => /^\d+$/.test(raw.trim()) && Number(raw) >= lo && Number(raw) <= hi

// The first problem with the form, or null when it can be sent.
export function validateProfile(f: ProfileForm, fields: ProfileField[]): string | null {
  if (!f.name.trim()) return 'Give the profile a name.'
  if (f.name.trim().length > 100) return 'A profile name is at most 100 characters.'
  if (!f.engine) return 'Choose a translation engine.'
  for (const [label, raw, lo, hi] of [
    ['Batch size', f.batch_size, 1, 60], ['Context window', f.context_window, 0, 100],
    ['Context window ahead', f.context_window_ahead, 0, 100],
  ] as const) {
    if (raw.trim() && !whole(raw, lo, hi)) return `${label} is a whole number from ${lo} to ${hi}, or blank.`
  }
  if (f.cost_cap.trim() && !(Number.isFinite(Number(f.cost_cap)) && Number(f.cost_cap) >= 0)) {
    return 'The cost cap is a dollar amount of 0 or more, or blank.'
  }
  for (const spec of fields) {
    const raw = (f.title[spec.key] ?? '').trim()
    if (!raw || (spec.type !== 'int' && spec.type !== 'number')) continue
    const n = Number(raw)
    const ok = Number.isFinite(n) && (spec.type === 'number' || Number.isInteger(n))
      && (spec.min === undefined || n >= spec.min) && (spec.max === undefined || n <= spec.max)
    if (!ok) return `${spec.label} must be a number from ${spec.min} to ${spec.max}, or blank.`
  }
  return null
}

export function toBody(f: ProfileForm, fields: ProfileField[]): TranslatePresetUpdateBody {
  const options: ProfileOptions = {}
  if (f.style_note.trim()) options.style_note = f.style_note.trim()
  if (f.reflect) options.reflect = true
  if (f.bulk) options.bulk = true
  if (f.thinking) options.thinking = true
  if (f.batch_size.trim()) options.batch_size = Number(f.batch_size)
  if (f.context_window.trim()) options.context_window = Number(f.context_window)
  if (f.context_window_ahead.trim()) options.context_window_ahead = Number(f.context_window_ahead)
  if (f.cost_cap.trim()) options.cost_cap = Number(f.cost_cap)
  if (f.fallbacks.length) options.fallback_engines = f.fallbacks
  for (const spec of fields) {
    const raw = (f.title[spec.key] ?? '').trim()
    if (!raw) continue
    options[spec.key] = spec.type === 'bool' ? raw === 'true' : spec.type === 'choice' ? raw : Number(raw)
  }
  return {
    name: f.name.trim(),
    translation_engine: f.engine,
    engine_model: f.model || null,
    style_preset: f.style_preset || null,
    locale: null,
    default_female_pronouns: f.female_pronouns,
    include_genre_notes: f.genre_notes,
    options,
  }
}
