// Pure wording for NovelChaptersPanel (the chapters saved in the raw novel).
import type { NovelChapterList, NovelChapterRow } from '../../../types/novelChapters'

export const EMPTY_CHAPTERS = 'No chapters saved yet. Import from Sources or paste text.'

const plural = (n: number, word: string) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`

/** "12 chapters, 48,210 characters", or the unsplit-file wording. */
export function chaptersHeadline(list: NovelChapterList | null): string {
  if (!list) return 'Checking…'
  if (!list.present || list.char_count === 0) return EMPTY_CHAPTERS
  if (!list.split) return `Unsplit text, ${plural(list.char_count, 'character')}`
  return `${plural(list.total, 'chapter')}, ${plural(list.char_count, 'character')}`
}

/** Section summary (visible while closed). */
export function chaptersSummary(list: NovelChapterList | null): string {
  if (!list) return 'checking'
  if (!list.present || list.char_count === 0) return 'none saved'
  return list.split ? plural(list.total, 'chapter') : 'unsplit'
}

/** How much of the saved text is in the text used for translation. */
export function translationLine(list: NovelChapterList): string {
  if (!list.present || list.char_count === 0) return ''
  const which = list.split ? plural(list.total, 'saved chapter') : 'the saved text'
  if (list.translation_chars === 0) return 'No text is attached for translation yet.'
  if (!list.split) {
    return list.in_translation ? 'The saved text is in the translation text.' : 'The saved text is not in the translation text.'
  }
  return `Translation text has ${list.in_translation.toLocaleString()} of ${which} (${plural(list.translation_chars, 'character')}).`
}

/** "Site · 2026-10-08" parts that are known; empty when neither is. */
export function rowMeta(row: NovelChapterRow): string {
  const date = row.imported_at ? row.imported_at.slice(0, 10) : ''
  return [row.source, date].filter(Boolean).join(' · ')
}

/** The visible name: the title, or "Chapter N" when the source gave none. */
export function rowTitle(row: NovelChapterRow): string {
  return row.title || `Chapter ${row.number}`
}

/** "Show all" is offered while the loaded slice stops short of the chapter. */
export function loadedLabel(loaded: number, total: number): string {
  return loaded >= total
    ? `${total.toLocaleString()} characters`
    : `${loaded.toLocaleString()} of ${total.toLocaleString()} characters`
}
