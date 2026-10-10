// /api/language-packs (api/schemas/language_packs.py).

export interface LanguagePackStyles {
  /** Empty, with no options, for a pack that has a single rendering. */
  default: string
  options: Record<string, string>
}

export interface LanguagePackSummary {
  id: string
  version: number
  /** zh, ja, ko, or any. */
  language: string
  title: string
  description: string
  entry_count: number
  styles: LanguagePackStyles
}

export interface LanguagePackEntry {
  source: string
  /** Text, or one rendering per style id. */
  en: string | Record<string, string>
  category: string
  note: string
  context: string
}

export interface LanguagePack extends LanguagePackSummary {
  entries: LanguagePackEntry[]
}

export interface TitleLanguagePack extends LanguagePackSummary {
  enabled: boolean
  style: string | null
}

export interface TitleLanguagePacks {
  source_language: string
  uses_default: boolean
  packs: TitleLanguagePack[]
}

/** Pack id -> style id (null for the pack's default). Packs left out are off. */
export interface LanguagePackChoice {
  packs: Record<string, string | null>
}

export interface LanguagePackDefaultResult {
  language: string
  packs: string[]
}
