import type { NovelLineChapterRow } from '../../types/novelChapters'

/** The page (1-based) a chapter's first line lands on. */
export const chapterPage = (firstIdx: number, pageSize: number) => Math.floor(firstIdx / pageSize) + 1

/** The chapter holding the first line of a page, or null before the list loads. */
export function chapterAt(chapters: NovelLineChapterRow[], page: number, pageSize: number) {
  const idx = (page - 1) * pageSize
  let hit: NovelLineChapterRow | null = null
  for (const c of chapters) if (c.first_idx <= idx) hit = c
  return hit
}
