// api/schemas/novel_chapters.py. Titles, counts and text only: the API
// never returns a filename, path or source URL.
export interface NovelChapterRow {
  number: number
  title: string
  chars: number
  source: string
  imported_at: string
  unsplit: boolean
  in_translation: boolean
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
  offset: number
  text: string
  next_offset: number | null
}
