// api/schemas/novel_chapters.py. Titles, counts and text, plus each chapter's
// display-safe source page link; never a filename or path.
export interface NovelChapterRow {
  number: number
  title: string
  chars: number
  source: string
  imported_at: string
  unsplit: boolean
  in_translation: boolean
  url?: string
}

export interface NovelChapterList {
  drama_id: number
  present: boolean
  size_bytes: number
  split: boolean
  total: number
  char_count: number
  in_translation: number
  translation_chars: number
  offset: number
  limit: number
  chapters: NovelChapterRow[]
}

export interface NovelChapterText {
  drama_id: number
  number: number
  title: string
  source: string
  imported_at: string
  unsplit: boolean
  chars: number
  in_translation: boolean
  url?: string
  offset: number
  text: string
  next_offset: number | null
}
