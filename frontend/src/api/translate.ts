import type {
  EngineList,
  TranslateDirection,
  TranslateEngine,
  TranslateHistoryEntry,
  TranslateRequest,
} from '../types/translate'
import { deleteJson, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

// The API accepts any text, language code and engine (no length caps in its
// schema); the app's own rule is that one side is always English.
export const NON_ENGLISH_LANGUAGES = [
  { code: 'zh', label: 'Chinese' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
]

export function languagePair(
  direction: TranslateDirection,
  other: string,
): { source_language: string; target_language: string } {
  return direction === 'to_english'
    ? { source_language: other, target_language: 'en' }
    : { source_language: 'en', target_language: other }
}

/** What a model picker shows for `model`: its server label when it has one (a model
 *  offered from the provider's list), else the id. */
export function modelOptionLabel(engine: { model_labels?: Record<string, string> } | null | undefined, model: string): string {
  return engine?.model_labels?.[model] ?? model
}

// Engines that can run now (key configured). The Translate page lists these
// first and marks the rest "(no key)"; the server answers 503 for those.
export function usableEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return engines.filter((e) => e.key_configured)
}

const ENGINE_DISPLAY_NAMES: Record<string, string> = {
  claude: 'Claude',
  deepseek: 'DeepSeek',
  gemini: 'Gemini',
  ollama: 'Ollama (local)',
  nllb: 'NLLB (offline)',
  libretranslate: 'LibreTranslate',
}

// The API's `label` is a long description, not a name, so the picker shows
// a short display name derived from the engine id (unknown ids are title-cased).
export function engineShortName(engine: Pick<TranslateEngine, 'name'>): string {
  const known = ENGINE_DISPLAY_NAMES[engine.name]
  if (known) return known
  const words = engine.name.replace(/[_-]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : engine.name
}

// One-line description of the chosen engine: the first sentence of the text
// before the first " -- ", capped so a long note can't blow up the layout.
export function engineSummary(label: string): string {
  const first = label.split(' -- ')[0].trim()
  const sentence = first.match(/^.*?[.!?](\s|$)/)?.[0].trim() ?? first
  return sentence.length > 140 ? `${sentence.slice(0, 137)}…` : sentence
}

// TranslateRequest.text max_length in api/schemas.py (the API answers 422 past it).
export const MAX_TRANSLATE_TEXT_CHARS = 2_000_000

export function validateTranslateInput(text: string, engine: string): string | null {
  if (!text.trim()) return 'Enter some text to translate.'
  if (text.length > MAX_TRANSLATE_TEXT_CHARS) {
    return `The text is too long: the limit is ${MAX_TRANSLATE_TEXT_CHARS.toLocaleString('en-US')} characters.`
  }
  if (!engine) return 'Pick an engine.'
  return null
}

export const translateApi = {
  engineList: (f?: Fetch) => getJson<EngineList>('/api/translate/engines', f),
  engines: (f?: Fetch) => translateApi.engineList(f).then((r) => r.items),
  history: (limit = 50, f?: Fetch) =>
    getJson<{ items: TranslateHistoryEntry[] }>(`/api/translate/history?limit=${limit}`, f).then(
      (r) => r.items,
    ),
  translate: (req: TranslateRequest, f?: Fetch) =>
    postJson<{ translated_text: string }>('/api/translate', req, f).then((r) => r.translated_text),
  // PC only (local_only route); the server refuses without confirm=true.
  clearHistory: (f?: Fetch) =>
    deleteJson<{ cleared: boolean }>('/api/translate/history?confirm=true', pcOnlyFetch(f)),
}
