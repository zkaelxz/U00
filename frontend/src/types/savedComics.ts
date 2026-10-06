// Saved manga (/api/saved-comics): chapters saved as CBZ files, read in the
// app. Series and chapters are named, never given as paths; only the PC-only
// folder routes return a path (the PC's own save folder).

export interface SavedComicsFolder {
  folder: string
  // The owner picked this folder (false: the default in the app's data folder).
  custom: boolean
  // A picked folder is gone (e.g. an unplugged drive), so saves use the default.
  picked_missing: boolean
}

export interface SavedSeries {
  source: string
  series: string
  chapter_count: number
  // Seconds since the epoch: when a chapter was last saved.
  updated_at: number | null
}

export interface SavedChapter {
  chapter: string
  title: string
  number: number | null
}

export interface SavedChapterList {
  source: string
  series: string
  chapters: SavedChapter[]
}

export interface SavedPage {
  page: number
  width: number | null
  height: number | null
}

export interface SavedChapterPages extends SavedChapter {
  source: string
  series: string
  pages: SavedPage[]
  prev_chapter: string | null
  next_chapter: string | null
}
