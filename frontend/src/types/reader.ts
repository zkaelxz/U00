// Hand-written mirrors of api/schemas/reader.py (Reader models).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export interface ReaderPage {
  html: string
  page: number
  page_count: number
  total_lines: number
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
  readings: boolean
  // Single lines with an edit button instead of joined paragraphs.
  edit?: boolean
}

export type ReaderOverview = Schemas['ReaderOverview']

export type ReaderProgress = Schemas['ReaderProgress']

export type ReaderNotes = Schemas['ReaderNotes']

export type CaptionTrack = 'Source' | 'English' | 'Bilingual'

export type ReaderMediaAvailability = Schemas['ReaderMediaAvailability']

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

export type ReaderLookupResult = Schemas['ReaderLookupResult']

export type ReaderVocabList = Schemas['ReaderVocabList']

export type ReaderRichExportResult = Schemas['ReaderRichExportResult']

export type ReaderAnswer = Schemas['ReaderAnswer']

export type ReaderRecap = Schemas['ReaderRecap']

export type ReaderRelationshipMap = Schemas['ReaderRelationshipMap']

export type ReaderWikiList = Schemas['ReaderWikiList']

export type ReaderWikiUpdateResult = Schemas['ReaderWikiUpdateResult']

export type ReaderWikiClearResult = Schemas['ReaderWikiClearResult']

export type ReaderChatTurn = Schemas['ReaderChatTurn']

// The rich sentence-card deck, fetched in JS so its headers can be read.
export interface RichDeckDownload {
  blob: Blob
  filename: string
  audioOmitted: boolean
  // Some cards lost their clip (ffmpeg failed or the time budget ran out).
  clipsMissing: boolean
  cardsCapped: number | null
}
