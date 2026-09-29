// Pure Reader helpers: the Aa preferences (kept in localStorage), paging,
// resume and the spoiler boundary. No React here so it is unit-tested.

import type { StorageLike } from '../../components/sectionStorage'
import type { ReaderChatTurn, ReaderOverview, ReaderPageParams } from '../../types/reader'

export type ReaderTheme = 'auto' | 'light' | 'dark'
export type ReaderFont = 'system' | 'serif' | 'sans-serif' | 'monospace'

export interface ReaderPrefs {
  theme: ReaderTheme
  font: ReaderFont
  fontSize: number
  lineHeight: number
  maxWidth: number
  chapterSize: number
  spoilerFree: boolean
}

export const PREFS_KEY = 'baihe.reader.prefs'

export const DEFAULT_PREFS: ReaderPrefs = {
  theme: 'auto',
  font: 'system',
  fontSize: 22,
  lineHeight: 2.4,
  maxWidth: 1200,
  chapterSize: 40,
  spoilerFree: true,
}

// Bounds match the API's query validation (api/routers/reader_routes.py).
export const FONT_SIZE = { min: 10, max: 48 }
export const CHAPTER_SIZE = { min: 10, max: 200, step: 10 }

export const THEME_OPTIONS: [ReaderTheme, string][] = [
  ['auto', 'Match app'],
  ['light', 'Light'],
  ['dark', 'Dark'],
]
export const FONT_OPTIONS: [ReaderFont, string][] = [
  ['system', 'System'],
  ['serif', 'Serif'],
  ['sans-serif', 'Sans serif'],
  ['monospace', 'Monospace'],
]
export const SPACING_OPTIONS: [number, string][] = [
  [1.6, 'Tight'],
  [2.0, 'Normal'],
  [2.4, 'Relaxed'],
  [3.0, 'Loose'],
]
export const WIDTH_OPTIONS: [number, string][] = [
  [700, 'Narrow'],
  [1000, 'Medium'],
  [1200, 'Wide'],
  [2400, 'Full'],
]

const clampInt = (v: unknown, min: number, max: number, fallback: number) =>
  typeof v === 'number' && Number.isFinite(v) ? Math.min(max, Math.max(min, Math.round(v))) : fallback

function pick<T>(v: unknown, options: [T, string][], fallback: T): T {
  return options.some(([o]) => o === v) ? (v as T) : fallback
}

/** Any stored value -> valid prefs; unknown or out-of-range fields fall back to the defaults. */
export function sanitizePrefs(raw: unknown): ReaderPrefs {
  const r = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const d = DEFAULT_PREFS
  return {
    theme: pick(r.theme, THEME_OPTIONS, d.theme),
    font: pick(r.font, FONT_OPTIONS, d.font),
    fontSize: clampInt(r.fontSize, FONT_SIZE.min, FONT_SIZE.max, d.fontSize),
    lineHeight: pick(r.lineHeight, SPACING_OPTIONS, d.lineHeight),
    maxWidth: pick(r.maxWidth, WIDTH_OPTIONS, d.maxWidth),
    chapterSize: roundChapterSize(r.chapterSize),
    spoilerFree: typeof r.spoilerFree === 'boolean' ? r.spoilerFree : d.spoilerFree,
  }
}

/** Lines per page: 10-200 in steps of 10. */
export function roundChapterSize(v: unknown): number {
  if (typeof v !== 'number' || !Number.isFinite(v)) return DEFAULT_PREFS.chapterSize
  const stepped = Math.round(v / CHAPTER_SIZE.step) * CHAPTER_SIZE.step
  return Math.min(CHAPTER_SIZE.max, Math.max(CHAPTER_SIZE.min, stepped))
}

export function loadPrefs(storage: StorageLike | null): ReaderPrefs {
  if (!storage) return { ...DEFAULT_PREFS }
  try {
    const raw = storage.getItem(PREFS_KEY)
    return raw === null ? { ...DEFAULT_PREFS } : sanitizePrefs(JSON.parse(raw))
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

export function savePrefs(storage: StorageLike | null, prefs: ReaderPrefs): boolean {
  if (!storage) return false
  try {
    storage.setItem(PREFS_KEY, JSON.stringify(prefs))
    return true
  } catch {
    return false
  }
}

/** Query for GET /page. Phones leave max_width out so the page fills the iframe. */
export function pageParams(
  prefs: ReaderPrefs,
  page: number,
  opts: { phone: boolean; prefersDark: boolean },
): ReaderPageParams {
  const theme = prefs.theme === 'auto' ? (opts.prefersDark ? 'dark' : 'light') : prefs.theme
  const p: ReaderPageParams = {
    page,
    chapter_size: prefs.chapterSize,
    theme,
    font_size: prefs.fontSize,
    line_height: prefs.lineHeight,
    font: prefs.font,
  }
  if (!opts.phone) p.max_width = prefs.maxWidth
  return p
}

export function pageCount(totalLines: number, chapterSize: number): number {
  return Math.max(1, Math.ceil(totalLines / chapterSize))
}

export const clampPage = (page: number, count: number) => Math.min(Math.max(1, Math.floor(page)), count)

/**
 * Where to resume: the page holding the last line read (so a changed
 * "Lines per page" still lands right), else the saved page. The server
 * reports a missing line as 0, so 0 falls back to the saved page.
 */
export function resumePage(ov: Pick<ReaderOverview, 'line_count' | 'last_page' | 'last_line_idx'>, chapterSize: number): number {
  const count = pageCount(ov.line_count, chapterSize)
  const page = ov.last_line_idx !== null && ov.last_line_idx > 0
    ? Math.floor(ov.last_line_idx / chapterSize) + 1
    : ov.last_page
  return clampPage(page || 1, count)
}

/** After "Lines per page" changes, the page that still shows the first line in view. */
export function keepPlace(page: number, oldSize: number, newSize: number): number {
  return Math.floor(((page - 1) * oldSize) / newSize) + 1
}

/**
 * Spoiler-free boundary: the last line of the current page. The saved
 * progress reports it exactly; until then it is estimated from the page.
 * undefined = no limit (Spoiler-free off).
 */
export function spoilerBoundary(
  spoilerFree: boolean,
  knownLastIdx: number | null,
  page: number,
  chapterSize: number,
  totalLines: number,
): number | undefined {
  if (!spoilerFree) return undefined
  if (knownLastIdx !== null && knownLastIdx >= 0) return knownLastIdx
  return Math.max(0, Math.min(page * chapterSize, totalLines) - 1)
}

/** "38% · about 2h 10m · 480 lines" */
export function metricsLine(ov: Pick<ReaderOverview, 'percent_complete' | 'length_display' | 'line_count'>): string {
  const pct = `${Math.round(ov.percent_complete)}%`
  const length = !ov.length_display ? '' : /^under\b/.test(ov.length_display) ? ov.length_display : `about ${formatLength(ov.length_display)}`
  return [pct, length, `${ov.line_count} lines`]
    .filter(Boolean)
    .join(' · ')
}

export const MAX_CHAT_TURNS = 40
// Server caps (api/schemas.py ReaderChatTurn/ReaderAskRequest): 20,000
// characters per turn, and about 100,000 across the history.
export const MAX_TURN_CHARS = 20_000
export const MAX_HISTORY_CHARS = 100_000

/**
 * Q&A history as the API accepts it: each turn cut to 20,000 characters,
 * then the oldest turns dropped until at most 40 turns and 100,000
 * characters remain.
 */
export function trimHistory(
  turns: ReaderChatTurn[],
  max = MAX_CHAT_TURNS,
  maxChars = MAX_HISTORY_CHARS,
): ReaderChatTurn[] {
  const out = turns.map((t) => (t.content.length > MAX_TURN_CHARS ? { ...t, content: t.content.slice(0, MAX_TURN_CHARS) } : t))
  let total = out.reduce((n, t) => n + t.content.length, 0)
  let start = Math.max(0, out.length - max)
  for (let i = 0; i < start; i++) total -= out[i].content.length
  while (start < out.length && total > maxChars) total -= out[start++].content.length
  return out.slice(start)
}

/** "3m" -> "3 min", "2h 10m" -> "2 h 10 min"; other text unchanged. */
export function formatLength(display: string): string {
  return display.replace(/(\d+)h\b/g, '$1 h').replace(/(\d+)m\b/g, '$1 min')
}
