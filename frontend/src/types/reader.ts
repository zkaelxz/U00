// Hand-written mirrors of api/schemas.py (Reader models, route batch 2B).

export interface ReaderPage {
  html: string
  page: number
  page_count: number
  total_lines: number
  // False when the optional word-splitting packages aren't installed.
  segmentation_available?: boolean
}

export interface ReaderPageParams {
  page: number
  chapter_size: number
  theme: 'light' | 'sepia' | 'dark'
  font_size: number
  line_height: number
  // Omitted on phones: the page then uses the server default and the iframe's own width.
  max_width?: number
  font: string
}

export interface ReaderOverview {
  drama_id: number
  length_display: string
  line_count: number
  percent_complete: number
  last_page: number
  last_line_idx: number | null
}

export interface ReaderProgress {
  drama_id: number
  last_page: number
  last_line_idx: number
  percent_complete: number
}

export interface ReaderNotes {
  drama_id: number
  notes: string
}

export type CaptionTrack = 'Source' | 'English' | 'Bilingual'

export interface ReaderMediaAvailability {
  drama_id: number
  original: 'video' | 'audio' | null
  dub: boolean
  narration: boolean
  caption_tracks: string[]
  captions_overlay: boolean
}

// Every LLM request. An omitted engine means Claude on the server and
// counts as paid, so the page always sends the chosen engine.
export interface ReaderEngineFields {
  engine?: string
  model?: string
}

export interface ReaderLookupRequest extends ReaderEngineFields {
  page: number
  chapter_size: number
  use_llm: boolean
}

export interface ReaderDefinition {
  reading: string | null
  definitions: string[]
}

export interface ReaderLookupResult {
  drama_id: number
  page: number
  definitions: Record<string, ReaderDefinition>
  saved: number
}

export interface ReaderVocabWord {
  word: string
  reading: string | null
  definitions: string[]
  language: string | null
  first_seen_line_idx: number | null
  export_rich: boolean
}

export interface ReaderVocabList {
  drama_id: number
  count: number
  words: ReaderVocabWord[]
}

export interface ReaderRichExportResult {
  drama_id: number
  updated: number
  queued: boolean
  rich_count: number
}

export interface ReaderAnswer {
  drama_id: number
  answer: string | null
}

export interface ReaderRecap {
  drama_id: number
  summary: string | null
  truncated: boolean
}

export interface ReaderRelationshipMap {
  drama_id: number
  characters: Record<string, unknown>[]
  relationships: Record<string, unknown>[]
  mermaid: string
}

export interface ReaderWikiEntry {
  id: number | null
  entry_type: string | null
  name: string | null
  aliases: unknown
  description: string | null
  attributes: Record<string, unknown>
  first_seen_line_idx: number | null
  known_through_line_idx: number | null
}

export interface ReaderWikiList {
  drama_id: number
  entry_types: string[]
  entries: ReaderWikiEntry[]
}

export interface ReaderWikiUpdateResult {
  drama_id: number
  updated: number
  remaining: number
  next_line_idx: number | null
}

export interface ReaderWikiClearResult {
  drama_id: number
  cleared: boolean
}

export interface ReaderChatTurn {
  role: 'user' | 'assistant'
  content: string
}

// The rich sentence-card deck, fetched in JS so its headers can be read.
export interface RichDeckDownload {
  blob: Blob
  filename: string
  audioOmitted: boolean
  cardsCapped: number | null
}
