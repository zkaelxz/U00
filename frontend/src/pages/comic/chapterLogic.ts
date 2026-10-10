// Pure chapter helpers for the comic viewer: which pages are in the reading
// list, chapter labels and counts, and the Translate scopes. Page numbers
// here are the server's `ordinal` (1-based over every page, hidden or not).

import type { StorageLike } from '../../components/sectionStorage'
import { readPref, writePref } from '../../hooks/usePersistedState'
import type { ComicChapter, ComicPageInfo } from '../../types/comic'

export interface ChapterPrefs {
  // Read only the selected chapter (default: continuous across chapters).
  chapterOnly: boolean
  // Show pages marked "not part of the story".
  showHidden: boolean
}

export const DEFAULT_CHAPTER_PREFS: ChapterPrefs = { chapterOnly: false, showHidden: false }

const prefsKey = (dramaId: number) => `comic.chapters.${dramaId}`

export function loadChapterPrefs(storage: StorageLike | null, dramaId: number): ChapterPrefs {
  const raw = readPref<Record<string, unknown>>(storage, prefsKey(dramaId), {})
  return {
    chapterOnly: raw.chapterOnly === true,
    showHidden: raw.showHidden === true,
  }
}

export function saveChapterPrefs(storage: StorageLike | null, dramaId: number, prefs: ChapterPrefs): boolean {
  return writePref(storage, prefsKey(dramaId), prefs)
}

export function chapterIndex(chapters: ComicChapter[], id: string | null | undefined): number {
  return id ? chapters.findIndex((c) => c.id === id) : -1
}

/** "Chapter 12" as the source titled it, else "Chapter N"; the no-data group is "Chapter unknown". */
export function chapterLabel(chapter: ComicChapter, index: number): string {
  if (!chapter.known) return 'Chapter unknown'
  return chapter.title || `Chapter ${index + 1}`
}

/** Pages of a chapter that are in the list: all of them, or without the hidden ones. */
export function shownCount(chapter: ComicChapter, showHidden: boolean): number {
  return showHidden ? chapter.page_count : chapter.page_count - chapter.hidden_count
}

export function optionText(chapter: ComicChapter, index: number, showHidden: boolean): string {
  const n = shownCount(chapter, showHidden)
  return `${chapterLabel(chapter, index)} (${n} ${n === 1 ? 'page' : 'pages'})`
}

/** Ordinals in the reading list, in order. Hidden pages drop out unless shown; a chapter limit keeps one chapter. */
export function visibleOrdinals(pages: ComicPageInfo[], prefs: ChapterPrefs, chapterId: string | null): number[] {
  const out: number[] = []
  pages.forEach((p, i) => {
    if (p.hidden && !prefs.showHidden) return
    if (prefs.chapterOnly && chapterId && p.chapter_id !== chapterId) return
    out.push(i + 1)
  })
  return out
}

/**
 * The listed page nearest `n`, looking in direction `dir` first. `n` itself
 * when it is listed, or when nothing is (so an empty list never moves anyone).
 */
export function snapToVisible(n: number, visible: number[], dir: 1 | -1): number {
  if (visible.length === 0 || visible.includes(n)) return n
  const after = visible.find((v) => v > n)
  const before = [...visible].reverse().find((v) => v < n)
  const pick = dir === 1 ? (after ?? before) : (before ?? after)
  return pick ?? n
}

/** Where a chapter starts in the reading list: its first listed page. */
export function chapterStart(chapter: ComicChapter, visible: number[]): number {
  const set = new Set(visible)
  for (let n = chapter.first_page; n < chapter.first_page + chapter.page_count; n++) {
    if (set.has(n)) return n
  }
  return chapter.first_page
}

export interface ChapterPosition {
  // 1-based position among the chapter's listed pages, and how many there are.
  inChapter: number
  chapterTotal: number
  // Same over the whole reading list.
  overall: number
  overallTotal: number
}

export function positionOf(pages: ComicPageInfo[], current: number, visible: number[]): ChapterPosition {
  const here = pages[current - 1]?.chapter_id
  const set = new Set(visible)
  let inChapter = 0
  let chapterTotal = 0
  pages.forEach((p, i) => {
    const n = i + 1
    if (!set.has(n) || p.chapter_id !== here) return
    chapterTotal++
    if (n <= current) inChapter++
  })
  const overall = visible.filter((n) => n <= current).length
  return { inChapter, chapterTotal, overall, overallTotal: visible.length }
}

export interface ScopeCount {
  // Pages the run covers (hidden pages never count).
  pages: number
  // Of those, pages with no text yet (what "Translate" does).
  todo: number
}

export interface ScopeCounts {
  chapter: (ScopeCount & { id: string; label: string }) | null
  all: ScopeCount
}

/** Page counts for the Translate scopes. `chapter` is null when there is no chapter to pick. */
export function scopeCounts(pages: ComicPageInfo[], chapters: ComicChapter[], chapterId: string | null): ScopeCounts {
  const count = (inScope: (p: ComicPageInfo) => boolean): ScopeCount => {
    const list = pages.filter((p) => !p.hidden && inScope(p))
    return { pages: list.length, todo: list.filter((p) => !p.has_regions).length }
  }
  const at = chapterIndex(chapters, chapterId)
  const chapter =
    at >= 0 && chapters.length > 1
      ? { id: chapters[at].id, label: chapterLabel(chapters[at], at), ...count((p) => p.chapter_id === chapterId) }
      : null
  return { chapter, all: count(() => true) }
}
