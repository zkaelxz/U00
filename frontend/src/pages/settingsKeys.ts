// Pure state and message logic for the Settings key form. Kept free of
// React so it is unit-testable; the key text lives only in `draft` and is
// dropped the moment a request is sent (success or failure).
import { ApiError } from '../api/client'
import { humanize } from '../components/labels'
import { ENDPOINTS } from './settings/preferences'
import { KEY_WRITES_REFUSED } from '../components/errorMessages'

// The engines the write endpoint accepts (services/settings_service.KEY_WRITE_ENGINES).
// URL settings (ollama_url etc.) are not secrets and have no write endpoint.
const SECRET_ENGINES: { engine: string; label: string }[] = [
  { engine: 'claude', label: 'Claude' },
  { engine: 'deepseek', label: 'DeepSeek' },
  { engine: 'gemini', label: 'Gemini' },
  { engine: 'deepl', label: 'DeepL' },
  { engine: 'google', label: 'Google Translate' },
  { engine: 'groq', label: 'Groq' },
  { engine: 'hf_token', label: 'Hugging Face' },
]

type KeyRow = { engine: string; label: string; writable: boolean }

/** The rows to show: write-only secrets first, then any other key the API reports (not the server addresses, which have their own block). */
export function keyRows(engineKeys: Record<string, boolean>): KeyRow[] {
  const secrets = SECRET_ENGINES.filter(({ engine }) => engine in engineKeys).map((e) => ({ ...e, writable: true }))
  const others = Object.keys(engineKeys)
    .filter((name) => !SECRET_ENGINES.some((e) => e.engine === name) && !ENDPOINTS.some((e) => e.name === name))
    .map((name) => ({ engine: name, label: humanize('engine', name), writable: false }))
  return [...secrets, ...others]
}

// Plain one-line messages; never the server's raw text and never the key.
export function keyErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return KEY_WRITES_REFUSED
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if (err.status === 422 || err.status === 400)
      return 'That key was not accepted. Paste it again without spaces or quotes.'
  }
  return 'The key could not be changed. Try again.'
}

export type KeyFormState = {
  draft: string
  confirm: 'none' | 'save' | 'clear'
  busy: boolean
  notice: string | null
  error: string | null
}

export const initialKeyForm: KeyFormState = {
  draft: '',
  confirm: 'none',
  busy: false,
  notice: null,
  error: null,
}

export type KeyFormAction =
  | { type: 'edit'; value: string }
  | { type: 'askSave' }
  | { type: 'askClear' }
  | { type: 'cancel' }
  | { type: 'send' }
  | { type: 'done'; notice: string }
  | { type: 'failed'; error: unknown }

export function keyFormReducer(s: KeyFormState, a: KeyFormAction): KeyFormState {
  switch (a.type) {
    case 'edit':
      return { ...s, draft: a.value, confirm: 'none', notice: null, error: null }
    case 'askSave':
      return s.draft.trim() ? { ...s, confirm: 'save', notice: null, error: null } : s
    case 'askClear':
      return { ...s, confirm: 'clear', notice: null, error: null }
    case 'cancel':
      return { ...s, confirm: 'none' }
    case 'send':
      // The caller has already captured the draft for the request; drop it now.
      return { ...s, draft: '', confirm: 'none', busy: true, notice: null, error: null }
    case 'done':
      return { ...initialKeyForm, notice: a.notice }
    case 'failed':
      return { ...initialKeyForm, error: keyErrorMessage(a.error) }
  }
}
