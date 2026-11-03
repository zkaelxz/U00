// Hand-written mirrors of api/schemas/translate.py and common.py (Translate-standalone models).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export interface TranslateEngine {
  name: string
  label: string
  free: boolean
  models: string[] | null
  // Labels for offered models that have no built-in entry (id -> text).
  model_labels?: Record<string, string>
  // Models that run on the provider's servers (Ollama cloud tags), not on this PC.
  cloud_models?: string[]
  // Can run: a key is set and the SDK is installed, or (Ollama) the server answers.
  key_configured: boolean
  // False when the engine's SDK is not installed (older servers omit it: treated as installed).
  package_installed?: boolean
}

export interface EngineList {
  items: TranslateEngine[]
  // Settings' default engine; the Translate page starts on it when it can run.
  default_engine: string | null
}

export type TranslateHistoryEntry = Schemas['TranslateHistoryEntry']

export type TranslateRequest = Schemas['TranslateRequest']

export interface TranslateResult {
  translated_text: string
  // Present only when the run stopped early because the next chunk would pass the monthly cap.
  message?: string | null
}

export type TranslateEstimate = Schemas['TranslateEstimate']

export type TranslateDirection = 'to_english' | 'from_english'

// POST /api/translate/read-image: the text written in a picture.
export interface ReadImageResult {
  text: string
  // Present only when nothing was found.
  message?: string | null
}

export type ReadImageEstimate = Schemas['ReadImageEstimate']
