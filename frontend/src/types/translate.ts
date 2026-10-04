// Hand-written mirrors of api/schemas/translate.py and common.py (Translate-standalone models).

export interface TranslateEngine {
  name: string
  label: string
  free: boolean
  models: string[] | null
  // Labels for offered models that have no built-in entry (id -> text).
  model_labels?: Record<string, string>
  key_configured: boolean
}

export interface EngineList {
  items: TranslateEngine[]
  // Settings' default engine; the Translate page starts on it when it can run.
  default_engine: string | null
}

export interface TranslateHistoryEntry {
  source_language: string
  target_language: string
  engine: string
  source_text: string
  translated_text: string
  created_at: string | null
}

export interface TranslateRequest {
  text: string
  engine: string
  source_language: string
  target_language: string
  model?: string | null
}

export type TranslateDirection = 'to_english' | 'from_english'
