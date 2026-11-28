import type { NovelLineChapterRow } from '../../types/novelChapters'

// Pages are a list of the line each one starts at (the page answer's page_starts): a novel's
// pages start at its chapters, so a page is not a fixed number of lines.

/** Pages of `size` lines, for before the server's own list has arrived. */
export const fixedStarts = (total: number, size: number): number[] =>
  Array.from({ length: Math.max(1, Math.ceil(total / Math.max(1, size))) }, (_, i) => i * size)

/** The page (1-based) that holds line `idx`. */
export function pageOfLine(starts: number[], idx: number): number {
  let lo = 0
  let hi = starts.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (starts[mid] <= idx) lo = mid
    else hi = mid - 1
  }
  return lo + 1
}

/** The first line of a page. */
export const firstLineOf = (starts: number[], page: number) => starts[page - 1] ?? 0

/** The last line of a page. */
export const lastLineOf = (starts: number[], page: number, total: number) =>
  Math.max(0, Math.min(starts[page] ?? total, total) - 1)

/** The page (1-based) a chapter's first line lands on. */
export const chapterPage = (firstIdx: number, starts: number[]) => pageOfLine(starts, firstIdx)

/** The chapter holding the first line of a page, or null before the list loads. */
export function chapterAt(chapters: NovelLineChapterRow[], page: number, starts: number[]) {
  const idx = firstLineOf(starts, page)
  let hit: NovelLineChapterRow | null = null
  for (const c of chapters) if (c.first_idx <= idx) hit = c
  return hit
}
