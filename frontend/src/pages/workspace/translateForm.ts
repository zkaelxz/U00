// Pure form logic for the Translate stage (no React), so it can be unit
// tested. Ranges mirror api/schemas.py TranslateRunStart.

import type {
  EstimateParams,
  FallbackEngine,
  TranslatePresetApplied,
  TranslatePresetBody,
  TranslateRunConfig,
  TranslateRunStartBody,
  WorkflowTierApplied,
} from '../../types/translateStage'

export const MAX_FALLBACKS = 2

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
// Mirrors translate_engines.TRANSLATION_ONLY_ENGINES.
const TRANSLATION_ONLY = ['nllb']

export function isTranslationOnly(engine: string): boolean {
  return TRANSLATION_ONLY.includes(engine)
}

export function reflectAvailable(engine: string): boolean {
  return !isTranslationOnly(engine)
}

// A fallback chain can't mix AI (instruction-following) engines with
// translation-only ones (services/translate_run_service.py refuses it).
export function sameEngineKind(a: string, b: string): boolean {
  return isTranslationOnly(a) === isTranslationOnly(b)
}

export const FALLBACK_KIND_MESSAGE =
  'Fallback engines must be the same kind as the main engine: AI engines with AI engines, translation-only with translation-only.'

// The engines fallback slot `slot` may offer: the same kind as the main
// engine, not the main engine itself, and not one chosen in another slot.
export function fallbackOptions(engines: string[], primary: string, fallbacks: string[], slot: number): string[] {
  const taken = new Set(fallbacks.filter((e, i) => i !== slot && e))
  return engines.filter((e) => e !== primary && !taken.has(e) && sameEngineKind(e, primary))
}

// True when a chosen fallback is a different kind from the main engine
// (e.g. the main engine was switched after the fallbacks were picked).
export function fallbackKindMismatch(primary: string, fallbacks: string[]): boolean {
  return fallbacks.some((e) => e && !sameEngineKind(e, primary))
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
interface PresetStart {
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
  // Then the Settings default English variant, then en-US.
  const fallbackLocale = c.default_locale && c.locales.includes(c.default_locale) ? c.default_locale : 'en-US'
  const locale = preset.locale && c.locales.includes(preset.locale)
    ? preset.locale
    : c.locales.includes(fallbackLocale) ? fallbackLocale : (c.locales[0] ?? 'en-US')
  const defaultModels = c.engines?.find((e) => e.name === c.translation_engine)?.models ?? []
  const model = preset.engine_model && defaultModels.includes(preset.engine_model) ? preset.engine_model : ''
  return {
    engine: '',
    model,
    style_preset: style,
    style_note: c.default_style_note ?? '',
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
  const chain = [eff, ...f.fallbacks]
  if (new Set(chain).size !== chain.length) return 'An engine cannot appear twice in the fallback chain.'
  if (fallbackKindMismatch(eff, f.fallbacks)) return FALLBACK_KIND_MESSAGE
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

// Parity X02: fill the form from an applied workflow tier, the way Streamlit's
// apply_workflow_tier set the engine, model and Reflect widgets. The engine is
// set explicitly (the drama's saved engine changed on the server, so the
// loaded config's default is stale). A model the engine doesn't list falls
// back to the engine default. Bulk is kept only if it still fits; nothing else
// (style, locale, toggles, fallbacks) changes, and no run is started.
export function applyTierToForm(f: RunForm, t: WorkflowTierApplied, c: TranslateRunConfig): RunForm {
  const engine = t.translation_engine
  const models = c.engines?.find((e) => e.name === engine)?.models ?? []
  const model = t.engine_model && models.includes(t.engine_model) ? t.engine_model : ''
  const reflect = t.reflect && reflectAvailable(engine)
  const supported = c.bulk_supported_engines ?? []
  const bulk = f.bulk && (reflect ? bulkReflectAvailable(engine, supported) : bulkAvailable(engine, supported))
  return { ...f, engine, model, reflect, bulk }
}

// After "Apply tier" the drama's saved engine is the tier's, so the loaded
// config's default is stale: "Default (...)", validation and a preset saved
// with Default selected must all use the new engine.
export function withSavedEngine(c: TranslateRunConfig, t: WorkflowTierApplied): TranslateRunConfig {
  return c.translation_engine === t.translation_engine ? c : { ...c, translation_engine: t.translation_engine }
}

// Parity X03: fill the form from an applied preset, the way Streamlit's
// apply_preset_to_session set the style/locale/toggle widgets. With an engine
// the preset's engine is set explicitly (the drama's saved engine changed on
// the server) and its model is kept only if that engine lists it; without
// one the engine and model stay as they are. Values this server no longer
// offers are skipped. Reflect/Bulk are kept only if they still fit. Nothing
// else changes and no run is started.
export function applyPresetToForm(f: RunForm, p: TranslatePresetApplied, c: TranslateRunConfig): RunForm {
  const engine = p.translation_engine || f.engine
  const eff = engine || c.translation_engine
  const models = c.engines?.find((e) => e.name === eff)?.models ?? []
  const model = p.translation_engine
    ? (p.engine_model && models.includes(p.engine_model) ? p.engine_model : '')
    : f.model
  const style = p.style_preset && c.style_presets.some((s) => s.key === p.style_preset) ? p.style_preset : f.style_preset
  const locale = p.locale && c.locales.includes(p.locale) ? p.locale : f.locale
  const supported = c.bulk_supported_engines ?? []
  const reflect = f.reflect && reflectAvailable(eff)
  const bulk = f.bulk && (reflect ? bulkReflectAvailable(eff, supported) : bulkAvailable(eff, supported))
  return {
    ...f,
    engine,
    model,
    style_preset: style,
    locale,
    reflect,
    bulk,
    female_pronouns: p.default_female_pronouns,
    genre_notes: p.include_genre_notes,
  }
}

// After a preset with an engine is applied, the drama's saved engine is the preset's.
export function withPresetEngine(c: TranslateRunConfig, p: TranslatePresetApplied): TranslateRunConfig {
  const e = p.translation_engine
  return !e || c.translation_engine === e ? c : { ...c, translation_engine: e }
}

// Parity X24: warn that Ollama can't be reached, only when the engine in use
// is Ollama and the server actually checked and got no answer. null/undefined
// means not checked (the drama's saved engine isn't Ollama), so no warning.
// A warning only: unlike Streamlit, Translate stays enabled.
export function ollamaWarning(effEngine: string, reachable: boolean | null | undefined): boolean {
  return effEngine === 'ollama' && reachable === false
}

// The guidance text for a style key ('' when the server sent none).
export function styleGuidance(c: TranslateRunConfig, key: string): string {
  return c.style_presets.find((s) => s.key === key)?.guidance ?? ''
}

export const PRESET_NAME_MAX = 100

export function validatePresetName(name: string): string | null {
  const t = name.trim()
  if (!t) return 'Give the preset a name.'
  if (t.length > PRESET_NAME_MAX) return `A preset name is at most ${PRESET_NAME_MAX} characters.`
  return null
}

// Parity X22: the same fields Streamlit's "Save as preset" captured (engine,
// model, style, locale, the she/her and genre toggles). A blank model means
// the engine default and is saved as null.
export function buildPresetBody(
  f: RunForm,
  defaultEngine: string,
  name: string,
  overwrite = false,
): TranslatePresetBody {
  return {
    name: name.trim(),
    translation_engine: f.engine || defaultEngine,
    engine_model: f.model || null,
    style_preset: f.style_preset || null,
    locale: f.locale || null,
    default_female_pronouns: f.female_pronouns,
    include_genre_notes: f.genre_notes,
    ...(overwrite ? { overwrite: true } : {}),
  }
}

// X01: Streamlit's notice for the last run's failed batches. Line numbers
// are 1-based and de-duplicated; tolerant of a malformed stored record
// (the API passes the stored JSON through as-is).
interface FailedBatches {
  batches: number
  lineNumbers: number[]
  reasons: string[]
}

export function failedBatches(errors: unknown): FailedBatches | null {
  if (!Array.isArray(errors) || errors.length === 0) return null
  const nums = new Set<number>()
  const reasons: string[] = []
  for (const e of errors) {
    if (!e || typeof e !== 'object') continue
    const { lines, error } = e as { lines?: unknown; error?: unknown }
    if (Array.isArray(lines)) for (const i of lines) if (Number.isInteger(i) && i >= 0) nums.add(i + 1)
    if (typeof error === 'string' && error.trim() && !reasons.includes(error.trim())) reasons.push(error.trim())
  }
  return { batches: errors.length, lineNumbers: [...nums].sort((a, b) => a - b), reasons }
}

// "1-3, 7, 9-10" so a long failure list stays one short line.
export function lineRanges(nums: number[]): string {
  const out: string[] = []
  for (let i = 0; i < nums.length; ) {
    let j = i
    while (j + 1 < nums.length && nums[j + 1] === nums[j] + 1) j++
    out.push(j === i ? String(nums[i]) : `${nums[i]}-${nums[j]}`)
    i = j + 1
  }
  return out.join(', ')
}
