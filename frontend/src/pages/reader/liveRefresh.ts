// Pure logic for the Reader's live refresh while a translation runs (useLiveRefresh.ts).
import type { NovelLineChapterRow } from '../../types/novelChapters'

export const LIVE_REFRESH_MS = 10_000

/** Changes whenever a chapter on this page gains or loses English. Chapters off the page don't count, so a
 * run working elsewhere in the book never reloads the page being read. Null when there are no chapters to go by. */
export function pageSignature(chapters: NovelLineChapterRow[], first: number, last: number): string | null {
  if (chapters.length === 0) return null
  const parts: string[] = []
  chapters.forEach((c, i) => {
    const next = chapters[i + 1]?.first_idx ?? Number.POSITIVE_INFINITY
    if (c.first_idx <= last && next > first) parts.push(`${c.number}:${c.translated}`)
  })
  return parts.join(',')
}

/** "Translating, chapter 12" for the chapter the run has reached (the last one that is part-translated),
 * or plain "Translating" before any chapter has English. */
export function translatingNote(chapters: NovelLineChapterRow[]): string {
  let at: NovelLineChapterRow | null = null
  for (const c of chapters) if (c.translated > 0 && c.translated < c.line_count) at = c
  return at ? `Translating, chapter ${at.number}` : 'Translating'
}
