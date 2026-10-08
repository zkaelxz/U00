import type { LineFilter, ReviewLine, ReviewMatch } from '../../../../types/review'
import { lineNumber } from '../../../../lineNumber'

export const PAGE_SIZE = 40

export function pageCount(total: number, pageSize: number = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

/** 1-based page holding the line at 0-based position `pos`. */
export function pageForPosition(pos: number, pageSize: number = PAGE_SIZE): number {
  return Math.floor(Math.max(0, pos) / pageSize) + 1
}

// Stale ids from an apply, shown as the line numbers the user sees.
export function staleLabels(staleIds: number[], matches: ReviewMatch[]): string[] {
  return staleIds.map((id) => {
    const m = matches.find((x) => x.id === id)
    return m ? `#${lineNumber(m.idx)}` : `line id ${id}`
  })
}

/** First flagged line, else first untranslated, else the first line. */
export function initialActiveId(lines: ReviewLine[]): number | null {
  const pick =
    lines.find((l) => l.flag) ?? lines.find((l) => l.zh.trim() && !l.en.trim()) ?? lines[0]
  return pick ? pick.id : null
}

type Step = { id: number } | { page: 'next' | 'prev' } | null

/** The line `delta` rows away from `activeId`, or a page change at an edge. */
export function stepFrom(lines: ReviewLine[], activeId: number | null, delta: 1 | -1, canPage: { next: boolean; prev: boolean }): Step {
  if (lines.length === 0) return null
  const i = lines.findIndex((l) => l.id === activeId)
  if (i === -1) return { id: (delta > 0 ? lines[0] : lines[lines.length - 1]).id }
  const j = i + delta
  if (j >= 0 && j < lines.length) return { id: lines[j].id }
  if (delta > 0 && canPage.next) return { page: 'next' }
  if (delta < 0 && canPage.prev) return { page: 'prev' }
  return null
}

/** Next/previous flagged line on the page after `fromIndex` (-1 = from the edge). */
export function nextFlaggedId(lines: ReviewLine[], fromIndex: number, delta: 1 | -1): number | null {
  if (fromIndex === -1) {
    const pick = delta > 0 ? lines.find((l) => l.flag) : [...lines].reverse().find((l) => l.flag)
    return pick ? pick.id : null
  }
  for (let j = fromIndex + delta; j >= 0 && j < lines.length; j += delta) if (lines[j].flag) return lines[j].id
  return null
}

/**
 * Where previous/next flagged looks (R08). A page of the "all" or "flagged"
 * view holds every flagged line near the start row, so it is searched first,
 * then the server from the page's edge. A page of a filtered view
 * ("untranslated") leaves flagged lines out, so the server is asked from the
 * focused row itself and none in between is skipped. `fromId` null = from
 * the far end.
 */
export function flaggedStep(
  lines: ReviewLine[],
  filter: LineFilter,
  fromIndex: number,
  delta: 1 | -1,
): { id: number } | { fromId: number | null } {
  if (filter !== 'all' && filter !== 'flagged' && fromIndex !== -1) return { fromId: lines[fromIndex].id }
  const id = nextFlaggedId(lines, fromIndex, delta)
  if (id !== null) return { id }
  const edge = delta > 0 ? lines[lines.length - 1] : lines[0]
  return { fromId: edge?.id ?? null }
}

/**
 * Interim guard until a line-index endpoint exists: before a structure edit the
 * whole id list is re-read; the edit only goes ahead when the loaded page still
 * sits where it was (filter "all": same ids at the same positions; other views:
 * the same ids, still in the same order).
 */
export function pageStillMatches(allIds: number[], pageIds: number[], filter: LineFilter | 'search', page: number, pageSize: number = PAGE_SIZE): boolean {
  if (filter === 'all') {
    const start = (page - 1) * pageSize
    const slice = allIds.slice(start, start + pageIds.length)
    return slice.length === pageIds.length && slice.every((id, i) => id === pageIds[i])
  }
  let last = -1
  for (const id of pageIds) {
    const pos = allIds.indexOf(id)
    if (pos <= last) return false
    last = pos
  }
  return true
}

/** `count` adjacent ids starting at `fromId`, or null when there are not enough. */
export function adjacentRun(allIds: number[], fromId: number, count: number): number[] | null {
  const i = allIds.indexOf(fromId)
  if (i === -1 || count < 2 || i + count > allIds.length) return null
  return allIds.slice(i, i + count)
}

export function lineRange(lines: Pick<ReviewLine, 'idx'>[]): string {
  if (lines.length === 0) return ''
  const a = lineNumber(lines[0].idx)
  const b = lineNumber(lines[lines.length - 1].idx)
  return a === b ? `#${a}` : `#${a}–#${b}`
}

export function chipLabel(filter: LineFilter, count: number | null, short: boolean): string {
  const n = count === null ? '' : ` ${count}`
  if (filter === 'all') return `All${n}`
  if (filter === 'flagged') return short ? `⚑${n}` : `Flagged${n}`
  return short ? `Untr.${n}` : `Untranslated${n}`
}

export function emptyMessage(filter: LineFilter, term: string): string {
  if (term) return `No lines match “${term}”.`
  if (filter === 'flagged') return 'No flagged lines. Nice.'
  if (filter === 'untranslated') return 'Every line has English.'
  return 'No lines yet. Transcribe on Source first.'
}
