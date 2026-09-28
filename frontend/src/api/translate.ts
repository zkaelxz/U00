import type {
  TranslateDirection,
  TranslateEngine,
  TranslateHistoryEntry,
  TranslateRequest,
} from '../types/translate'
import { getJson, postJson } from './client'

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

// Engines the picker offers. A missing key is a Settings problem, so those
// stay out; the server still answers 503 if the list was stale.
export function usableEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return engines.filter((e) => e.key_configured)
}

export function validateTranslateInput(text: string, engine: string): string | null {
  if (!text.trim()) return 'Enter some text to translate.'
  if (!engine) return 'Pick an engine.'
  return null
}

export const translateApi = {
  engines: (f?: Fetch) =>
    getJson<{ items: TranslateEngine[] }>('/api/translate/engines', f).then((r) => r.items),
  history: (limit = 50, f?: Fetch) =>
    getJson<{ items: TranslateHistoryEntry[] }>(`/api/translate/history?limit=${limit}`, f).then(
      (r) => r.items,
    ),
  translate: (req: TranslateRequest, f?: Fetch) =>
    postJson<{ translated_text: string }>('/api/translate', req, f).then((r) => r.translated_text),
}
