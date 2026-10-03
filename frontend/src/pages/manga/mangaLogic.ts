// Pure helpers for saved manga (chapters saved as CBZ files): links, the
// per-series view settings and where the reader left off. Both live in this
// browser's localStorage, keyed by source and series name (names never hold
// "/", so the joined key is unambiguous). No React here so it is unit-tested.

import type { StorageLike } from '../../components/sectionStorage'
import { readPref, writePref } from '../../hooks/usePersistedState'
import { routeHref } from '../../router'
import type { ComicPageInfo } from '../../types/comic'
import type { SavedPage } from '../../types/savedComics'
import { sanitizePrefs, type ComicPrefs } from '../comic/comicLogic'

export interface LastRead {
  chapter: string
  page: number
}

// Saved chapters have no typeset pages or text boxes; vertical scroll suits
// both long-strip manhwa and manga, and "Aa" switches to one page at a time.
export const MANGA_DEFAULTS: ComicPrefs = { mode: 'vertical', rtl: false, fit: 'width', typeset: false, text: false }

const seriesKey = (source: string, series: string) => `${source}/${series}`

export function loadMangaPrefs(storage: StorageLike | null, source: string, series: string): ComicPrefs {
  const raw = readPref<Record<string, unknown>>(storage, `manga.view.${seriesKey(source, series)}`, {})
  return { ...sanitizePrefs(raw, MANGA_DEFAULTS), typeset: false, text: false }
}

export function saveMangaPrefs(storage: StorageLike | null, source: string, series: string, prefs: ComicPrefs): boolean {
  return writePref(storage, `manga.view.${seriesKey(source, series)}`, prefs)
}

export function loadLastRead(storage: StorageLike | null, source: string, series: string): LastRead | null {
  const raw = readPref<Record<string, unknown>>(storage, `manga.last.${seriesKey(source, series)}`, {})
  const { chapter, page } = raw
  if (typeof chapter !== 'string' || !chapter || typeof page !== 'number' || !Number.isInteger(page) || page < 1) return null
  return { chapter, page }
}

export function saveLastRead(storage: StorageLike | null, source: string, series: string, last: LastRead): boolean {
  return writePref(storage, `manga.last.${seriesKey(source, series)}`, last)
}

export const mangaSeriesHref = (source: string, series: string) => routeHref({ name: 'manga-series', source, series })

export const mangaReadHref = (source: string, series: string, chapter: string, page: number | null = null) =>
  routeHref({ name: 'manga-read', source, series, chapter, page })

// The comic viewer's page shape: a saved page's number is its id, and it is
// never typeset and has no text boxes.
export function pageInfos(pages: SavedPage[]): ComicPageInfo[] {
  return pages.map((p) => ({
    id: p.page, ordinal: p.page, width: p.width, height: p.height,
    has_rendered: false, has_regions: false, image_version: null,
  }))
}

export function chapterCount(n: number): string {
  return `${n} chapter${n === 1 ? '' : 's'}`
}
