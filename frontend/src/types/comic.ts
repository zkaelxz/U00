// Comic viewer API shapes (/api/scanlate/dramas/{id}/..., routes C1-C5 in the
// comic viewer spec). No filenames or paths ever come back from these routes.

export type ComicVariant = 'original' | 'rendered'

export interface ComicPageInfo {
  id: number
  // 1-based page number in the drama (page 1 is ordinal 1).
  ordinal: number
  width: number | null
  height: number | null
  has_rendered: boolean
  has_regions: boolean
  // Changes when the file is rewritten (e.g. re-typeset); used as a cache-buster.
  image_version: number | string | null
}

export interface ComicChapter {
  label: string
  start_ordinal: number
}

// C1: GET /pages
export interface ComicPagesResponse {
  drama_id: number
  media_type: string | null
  // Always sent: 'paged' for manga, 'vertical' for everything else.
  reading_mode_default: 'paged' | 'vertical'
  page_count: number
  pages: ComicPageInfo[]
  // Always empty until chapter markers exist; the viewer uses a page scrubber.
  chapters: ComicChapter[]
}

export interface ComicRegion {
  idx: number
  x: number
  y: number
  w: number
  h: number
  translated_text: string | null
  source_text: string | null
  kind: string | null
}

// C3: GET /pages/{page_id}/regions (boxes in original-image pixels)
export interface ComicRegionsResponse {
  page_id: number
  width: number | null
  height: number | null
  regions: ComicRegion[]
}

// C4 / C5: GET and POST /progress (last_page is 1-based)
export interface ComicProgress {
  last_page: number
  percent_complete: number | null
}
