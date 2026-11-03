// Comic viewer API shapes (/api/scanlate/dramas/{id}: pages, regions
// and progress). No filenames or paths ever come back from these routes.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

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
  // The chapter group this page is in ('unknown' for pages with no chapter data).
  // Absent from older servers.
  chapter_id?: string | null
  // 1-based position within the chapter group.
  chapter_page?: number
  // Marked "not part of the story" (credits, promos). Still listed here.
  hidden?: boolean
}

export interface ComicChapter {
  id: string
  // As given by the source; '' when it gave none.
  title: string
  // False for the one group of pages with no chapter data (older imports).
  known: boolean
  // 1-based ordinal of the chapter's first page.
  first_page: number
  page_count: number
  hidden_count: number
  // The chapter's page on its source (scheme, host and path only); '' or absent when not known.
  url?: string
}

// C1: GET /pages
export interface ComicPagesResponse {
  drama_id: number
  media_type: string | null
  // Always sent: 'paged' for manga, 'vertical' for everything else.
  reading_mode_default: 'paged' | 'vertical'
  page_count: number
  pages: ComicPageInfo[]
  hidden_count?: number
  // Reading-order chapter groups; empty from older servers.
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
export type ComicProgress = Schemas['ComicProgress']

// GET and POST /view-prefs: how the title was last shown, kept on the server so it follows the reader to another device.
export type ComicViewPrefsView = Schemas['ComicViewPrefsView']
export type ComicViewPrefs = Schemas['ComicViewPrefs']

// POST /pages/visibility: page ids, or the first/last `count` pages of a chapter.
export interface ComicVisibilityRequest {
  hidden: boolean
  page_ids?: number[]
  chapter_id?: string
  edge?: 'first' | 'last' | 'all'
  count?: number
}

export type ComicVisibilityResult = Schemas['ComicVisibilityResult']

export type ComicSourceChapter = Schemas['ComicSourceChapter']

export type ComicSourceChapters = Schemas['ComicSourceChapters']
