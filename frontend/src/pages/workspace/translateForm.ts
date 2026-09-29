// Pure form logic for the Translate stage (no React), so it can be unit
// tested. Ranges mirror api/schemas.py TranslateRunStart.

import type {
  EstimateParams,
  FallbackEngine,
  TranslateRunConfig,
  TranslateRunStartBody,
} from '../../types/translateStage'

export const MAX_FALLBACKS = 3

export interface RunForm {
  engine: string // '' = the drama's configured default engine
  model: string // '' = engine default
  style_preset: string
  style_note: string
  locale: string
  batch_size: string
  context_window: string
  context_window_ahead: string
  cost_cap: string // '' = no per-job cap
  fallbacks: string[] // engine names, in order
  force: boolean
  forceConfirmed: boolean
  reflect: boolean
  bulk: boolean
  female_pronouns: boolean // she/her default for ambiguous pronouns
  genre_notes: boolean // baihe/GL genre guidance in the prompt
}

// Engines that only translate (no free-form prompting) cannot run Reflect.
const TRANSLATION_ONLY = ['deepl', 'google', 'nllb', 'libretranslate']

export function reflectAvailable(engine: string): boolean {
  return !TRANSLATION_ONLY.includes(engine)
}

export function bulkAvailable(engine: string, supported: string[]): boolean {
  return supported.includes(engine)
}

// Bulk Reflect needs a batch API (Claude or Gemini), not DeepSeek off-peak.
export function bulkReflectAvailable(engine: string, supported: string[]): boolean {
  return bulkAvailable(engine, supported) && engine !== 'deepseek'
}

// A drama's preset values (POST /api/dramas preset_defaults) that the
// Translate form starts from. engine_model belongs to the drama's saved
// engine (the preset's), so it only applies while that engine is used.
export interface PresetStart {
  style_preset?: string
  locale?: string
  default_female_pronouns?: boolean
  include_genre_notes?: boolean
  engine_model?: string
}

interface PresetDefaultsIn {
  style_preset?: string | null
  locale?: string | null
  default_female_pronouns?: boolean | null
  include_genre_notes?: boolean | null
  engine_model?: string | null
}

const presetKey = (dramaId: number) => `baihe.translatePreset.${dramaId}`

// Kept per drama in localStorage (the API only saves the preset's engine on
// the drama), so the Translate stage starts from them in any later visit.
export function savePresetStart(dramaId: number, d: PresetDefaultsIn | null | undefined): void {
  const out: PresetStart = {}
  if (d?.style_preset) out.style_preset = d.style_preset
  if (d?.locale) out.locale = d.locale
  if (typeof d?.default_female_pronouns === 'boolean') out.default_female_pronouns = d.default_female_pronouns
  if (typeof d?.include_genre_notes === 'boolean') out.include_genre_notes = d.include_genre_notes
  if (d?.engine_model) out.engine_model = d.engine_model
  try {
    // No preset values: clear any stale entry (a reused drama id).
    if (Object.keys(out).length) localStorage.setItem(presetKey(dramaId), JSON.stringify(out))
    else localStorage.removeItem(presetKey(dramaId))
  } catch {
    // storage unavailable: the form just starts from the global defaults
  }
}

export function loadPresetStart(dramaId: number): PresetStart {
  try {
    const raw = localStorage.getItem(presetKey(dramaId))
    const v: unknown = raw ? JSON.parse(raw) : null
    if (!v || typeof v !== 'object') return {}
    const o = v as Record<string, unknown>
    const out: PresetStart = {}
    if (typeof o.style_preset === 'string') out.style_preset = o.style_preset
    if (typeof o.locale === 'string') out.locale = o.locale
    if (typeof o.default_female_pronouns === 'boolean') out.default_female_pronouns = o.default_female_pronouns
    if (typeof o.include_genre_notes === 'boolean') out.include_genre_notes = o.include_genre_notes
    if (typeof o.engine_model === 'string') out.engine_model = o.engine_model
    return out
  } catch {
    return {}
  }
}

// A preset value only applies when this server still offers it. Without a
// preset the toggles start as the Workspace checkboxes did: she/her off,
// genre notes on (also the API's default when they are omitted).
export function initialForm(c: TranslateRunConfig, preset: PresetStart = {}): RunForm {
  const style = preset.style_preset && c.style_presets.some((p) => p.key === preset.style_preset)
    ? preset.style_preset
    : c.default_style_preset
  const locale = preset.locale && c.locales.includes(preset.locale)
    ? preset.locale
    : c.locales.includes('en-US') ? 'en-US' : (c.locales[0] ?? 'en-US')
  const defaultModels = c.engines?.find((e) => e.name === c.translation_engine)?.models ?? []
  const model = preset.engine_model && defaultModels.includes(preset.engine_model) ? preset.engine_model : ''
  return {
    engine: '',
    model,
    style_preset: style,
    style_note: '',
    locale,
    batch_size: String(c.defaults.batch_size),
    context_window: String(c.defaults.context_window),
    context_window_ahead: String(c.defaults.context_window_ahead),
    cost_cap: '',
    fallbacks: [],
    force: false,
    forceConfirmed: false,
    reflect: false,
    bulk: false,
    female_pronouns: preset.default_female_pronouns ?? false,
    genre_notes: preset.include_genre_notes ?? true,
  }
}

function intIn(raw: string, min: number, max: number): number | null {
  const t = raw.trim()
  if (!/^\d+$/.test(t)) return null
  const n = Number(t)
  return n >= min && n <= max ? n : null
}

// undefined = blank (no cap), null = invalid, number = a valid cap.
export function parseCap(raw: string): number | null | undefined {
  const t = raw.trim()
  if (t === '') return undefined
  const n = Number(t)
  return Number.isFinite(n) && n >= 0 ? n : null
}

export function validateRun(
  f: RunForm,
  defaultEngine: string,
  bulkSupported: string[] = [],
): string | null {
  const eff = f.engine || defaultEngine
  if (f.reflect && !reflectAvailable(eff)) return `${eff} cannot run Reflect.`
  if (f.bulk && !bulkAvailable(eff, bulkSupported)) return 'Bulk needs Claude, Gemini or DeepSeek.'
  if (f.bulk && f.reflect && !bulkReflectAvailable(eff, bulkSupported)) return 'Bulk Reflect needs Claude or Gemini.'
  if ((f.bulk || f.reflect) && f.fallbacks.length) return 'Fallback engines only apply to a normal run.'
  if (intIn(f.batch_size, 1, 200) === null) return 'Batch size must be a whole number from 1 to 200.'
  if (intIn(f.context_window, 0, 100) === null) return 'Context window must be a whole number from 0 to 100.'
  if (intIn(f.context_window_ahead, 0, 100) === null)
    return 'Context window ahead must be a whole number from 0 to 100.'
  if (parseCap(f.cost_cap) === null) return 'Cost cap must be a number of dollars, 0 or more.'
  if (f.style_note.length > 4000) return 'Style note is too long (4000 characters at most).'
  if (f.fallbacks.length > MAX_FALLBACKS) return `At most ${MAX_FALLBACKS} fallback engines.`
  if (f.fallbacks.some((e) => !e)) return 'Choose an engine for every fallback slot or remove it.'
  const chain = [f.engine || defaultEngine, ...f.fallbacks]
  if (new Set(chain).size !== chain.length) return 'An engine cannot appear twice in the fallback chain.'
  if (f.force && !f.forceConfirmed) return 'Tick the confirmation box to replace existing English text.'
  return null
}

// Call only after validateRun returned null. line_ids is left out on purpose.
export function buildRunBody(f: RunForm): TranslateRunStartBody {
  const cap = parseCap(f.cost_cap)
  const chain: FallbackEngine[] = f.fallbacks.map((engine) => ({ engine }))
  return {
    ...(f.engine ? { engine: f.engine } : {}),
    ...(f.model ? { model: f.model } : {}),
    ...(f.style_preset ? { style_preset: f.style_preset } : {}),
    style_note: f.style_note,
    locale: f.locale,
    force_retranslate: f.force && f.forceConfirmed,
    context_window: Number(f.context_window),
    context_window_ahead: Number(f.context_window_ahead),
    batch_size: Number(f.batch_size),
    ...(typeof cap === 'number' ? { job_cost_cap_usd: cap } : {}),
    ...(chain.length ? { fallback_chain: chain } : {}),
    ...(f.reflect ? { reflect: true } : {}),
    ...(f.bulk ? { bulk: true } : {}),
    default_female_pronouns: f.female_pronouns,
    include_genre_notes: f.genre_notes,
  }
}

// null while the cap field is invalid (no estimate is requested then).
export function buildEstimateParams(f: RunForm): EstimateParams | null {
  const cap = parseCap(f.cost_cap)
  if (cap === null) return null
  return {
    engine: f.engine || undefined,
    model: f.model || undefined,
    force_retranslate: f.force && f.forceConfirmed,
    job_cost_cap_usd: cap,
    ...(f.reflect ? { reflect: true } : {}),
    ...(f.bulk ? { bulk: true } : {}),
  }
}

// A monthly cap of 0 (or less) means "no cap" (translate_engines.resolve_cost_cap),
// so it must not read as "$X of $0.00", which looks like the cap was reached.
export function monthSpendText(spend: number, cap: number): string {
  return cap > 0
    ? `Spend this month: $${spend.toFixed(2)} of $${cap.toFixed(2)}.`
    : `Spent this month: $${spend.toFixed(2)} (no monthly cap).`
}

// One entry per line; blanks dropped, duplicates removed, order kept.
export function splitLines(raw: string): string[] {
  return [...new Set(raw.split('\n').map((s) => s.trim()).filter(Boolean))]
}
