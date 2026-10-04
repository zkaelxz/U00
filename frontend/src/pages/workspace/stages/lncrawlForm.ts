// The "Import with lightnovel-crawler" form, checked before it is
// sent. The server checks everything again (and that the host is public).
import type { LncrawlChapters, LncrawlImportRequest, NovelMode } from '../../../types/workspace'

export const LNCRAWL_MAX_CHAPTERS = 5000

export function lncrawlRequest(
  url: string,
  chapters: LncrawlChapters,
  countText: string,
  mode: NovelMode,
): { body: LncrawlImportRequest } | { problem: string } {
  const u = url.trim()
  if (!u) return { problem: "Paste the novel's web address." }
  const hasControlOrSpace = [...u].some((c) => c.charCodeAt(0) <= 32 || c.charCodeAt(0) === 127)
  if (!/^https?:\/\/[^\s/?#]+/i.test(u) || hasControlOrSpace) {
    return { problem: 'The address must start with http:// or https://.' }
  }
  if (u.length > 2000) return { problem: 'The address is too long.' }
  if (chapters === 'all') return { body: { url: u, chapters, mode } }
  const n = Number(countText.trim())
  if (!countText.trim() || !Number.isInteger(n) || n < 1 || n > LNCRAWL_MAX_CHAPTERS) {
    return { problem: `Pick how many chapters, from 1 to ${LNCRAWL_MAX_CHAPTERS}.` }
  }
  return { body: { url: u, chapters, count: n, mode } }
}

/** "Imported 12,345 characters (40 EPUB sections)." from the job's result. */
export function lncrawlNotice(result: Record<string, unknown> | null | undefined): string {
  const chars = typeof result?.char_count === 'number' ? result.char_count : null
  const sections = typeof result?.epub_chapters === 'number' ? result.epub_chapters : null
  if (chars === null) return 'Imported.'
  const tail = sections ? ` (${sections} EPUB sections)` : ''
  return `Imported ${chars.toLocaleString()} characters${tail}.`
}
