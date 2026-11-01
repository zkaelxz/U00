// api/schemas/title_restructure.py. Ids, names and counts only; never a
// path or stored filename.
interface TitleRestructureChapter {
  // Comic: the chapter group's id ("unknown" for unlabelled pages); novel: its number as text.
  id: string
  title: string
  pages: number | null
  chars: number | null
}

interface TitleRestructureTarget {
  id: number
  title: string
}

export interface TitleRestructureInfo {
  drama_id: number
  kind: 'comic' | 'novel' | null
  chapters: TitleRestructureChapter[]
  targets: TitleRestructureTarget[]
}

export type TitleRestructureKind = 'merge' | 'move'

export interface TitleRestructureRequest {
  target_id: number
  kind: TitleRestructureKind
  chapter_ids?: string[]
  move_duplicates?: boolean
}

interface TitleRestructurePreviewChapter extends TitleRestructureChapter {
  duplicate: boolean
  moves: boolean
  in_translation: boolean
}

export interface TitleRestructurePreview {
  source_id: number
  target_id: number
  kind: TitleRestructureKind
  media: 'comic' | 'novel' | null
  target_title: string
  chapters: TitleRestructurePreviewChapter[]
  moving_chapters: number
  moving_pages: number
  skipped_duplicates: number
  source_empty_after: boolean
  stays: string[]
  notes: string[]
  blocked: string[]
  fingerprint: string
}

export interface TitleRestructureResult {
  source_id: number
  target_id: number
  kind: TitleRestructureKind
  moved_chapters: number
  moved_pages: number
  skipped_duplicates: number
  source_empty: boolean
  snapshot: string
  warnings: string[]
}
