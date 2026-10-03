import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import { BUSY_TEXT, FORBIDDEN_TEXT, PAID_TEXT, readerErrorText } from './readerErrors'
import {
  DEFAULT_PREFS,
  PREFS_KEY,
  formatLength,
  keepPlace,
  loadPrefs,
  metricsLine,
  pageCount,
  pageParams,
  resumePage,
  roundChapterSize,
  sanitizePrefs,
  savePrefs,
  spoilerBoundary,
  trimHistory,
} from './readerPrefsStore'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
  }
}

describe('reader prefs', () => {
  it('round-trips through storage under baihe.reader.prefs', () => {
    const s = memoryStorage()
    expect(savePrefs(s, { ...DEFAULT_PREFS, fontSize: 30 })).toBe(true)
    expect(JSON.parse(s.data[PREFS_KEY]).fontSize).toBe(30)
    expect(loadPrefs(s).fontSize).toBe(30)
  })

  it('falls back to defaults on missing, broken or throwing storage', () => {
    expect(loadPrefs(null)).toEqual(DEFAULT_PREFS)
    expect(loadPrefs(memoryStorage({ [PREFS_KEY]: '{not json' }))).toEqual(DEFAULT_PREFS)
    const throwing = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    }
    expect(loadPrefs(throwing)).toEqual(DEFAULT_PREFS)
    expect(savePrefs(throwing, DEFAULT_PREFS)).toBe(false)
  })

  it('clamps and rejects bad stored values', () => {
    const p = sanitizePrefs({ theme: 'neon', font: 'serif', fontSize: 99, lineHeight: 7, maxWidth: 1000, chapterSize: 47, spoilerFree: 'no' })
    expect(p).toEqual({ ...DEFAULT_PREFS, font: 'serif', fontSize: 48, maxWidth: 1000, chapterSize: 50 })
    expect(sanitizePrefs(null)).toEqual(DEFAULT_PREFS)
    expect(sanitizePrefs({ fontSize: 2 }).fontSize).toBe(10)
  })

  it('keeps lines per page to 10-200 in steps of 10', () => {
    expect(roundChapterSize(3)).toBe(10)
    expect(roundChapterSize(44)).toBe(40)
    expect(roundChapterSize(999)).toBe(200)
    expect(roundChapterSize(Number.NaN)).toBe(40)
  })

  it('builds page params: Match app follows the app theme, phones omit max_width', () => {
    const desk = pageParams(DEFAULT_PREFS, 3, { phone: false, appLook: 'dark' })
    expect(desk).toEqual({ page: 3, chapter_size: 40, theme: 'dark', font_size: 22, line_height: 2.4, max_width: 1200, font: 'system' })
    expect(pageParams(DEFAULT_PREFS, 1, { phone: false, appLook: 'sepia' }).theme).toBe('sepia')
    expect(pageParams(DEFAULT_PREFS, 1, { phone: false, appLook: 'oled' }).theme).toBe('dark')
    const phone = pageParams({ ...DEFAULT_PREFS, theme: 'light' }, 1, { phone: true, appLook: 'dark' })
    expect(phone.theme).toBe('light')
    expect('max_width' in phone).toBe(false)
  })
})

describe('paging', () => {
  it('counts pages', () => {
    expect(pageCount(0, 40)).toBe(1)
    expect(pageCount(90, 40)).toBe(3)
    expect(pageCount(80, 40)).toBe(2)
  })

  it('resumes at the page holding the last line read, clamped', () => {
    expect(resumePage({ line_count: 90, last_page: 1, last_line_idx: null }, 40)).toBe(1)
    expect(resumePage({ line_count: 90, last_page: 2, last_line_idx: null }, 40)).toBe(2)
    expect(resumePage({ line_count: 90, last_page: 1, last_line_idx: 79 }, 40)).toBe(2)
    expect(resumePage({ line_count: 90, last_page: 1, last_line_idx: 80 }, 20)).toBe(5)
    expect(resumePage({ line_count: 90, last_page: 9, last_line_idx: 500 }, 40)).toBe(3)
    expect(resumePage({ line_count: 90, last_page: 2, last_line_idx: 0 }, 40)).toBe(2)
  })

  it('keeps the first line in view when lines per page changes', () => {
    expect(keepPlace(3, 40, 20)).toBe(5)
    expect(keepPlace(5, 20, 40)).toBe(3)
    expect(keepPlace(1, 40, 200)).toBe(1)
  })

  it('sets the spoiler boundary at the end of this page', () => {
    expect(spoilerBoundary(false, 50, 2, 40, 90)).toBeUndefined()
    expect(spoilerBoundary(true, 77, 2, 40, 90)).toBe(77)
    expect(spoilerBoundary(true, null, 2, 40, 90)).toBe(79)
    expect(spoilerBoundary(true, null, 3, 40, 90)).toBe(89)
  })

  it('formats the metrics line', () => {
    expect(metricsLine({ percent_complete: 37.6, length_display: '2h 10m', line_count: 480 })).toBe('38% · about 2 h 10 min · 480 lines')
    expect(metricsLine({ percent_complete: 0, length_display: 'under a minute', line_count: 3 })).toBe('0% · under a minute · 3 lines')
  })

  it('keeps the last 40 chat turns', () => {
    const turns = Array.from({ length: 44 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: String(i) }) as const)
    const trimmed = trimHistory(turns)
    expect(trimmed).toHaveLength(40)
    expect(trimmed[0].content).toBe('4')
    expect(trimHistory(turns.slice(0, 3))).toHaveLength(3)
  })

  it('drops the oldest turns to stay under the character caps', () => {
    const big = (c: string) => ({ role: 'user' as const, content: c.repeat(30_000) })
    const trimmed = trimHistory([big('a'), big('b'), big('c'), big('d'), big('e'), big('f')])
    expect(trimmed.every((t) => t.content.length === 20_000)).toBe(true)
    expect(trimmed.map((t) => t.content[0])).toEqual(['b', 'c', 'd', 'e', 'f'])
    expect(trimmed.reduce((n, t) => n + t.content.length, 0)).toBeLessThanOrEqual(100_000)
  })

  it('spells out lengths', () => {
    expect(formatLength('3m')).toBe('3 min')
    expect(formatLength('2h 10m')).toBe('2 h 10 min')
    expect(formatLength('2h')).toBe('2 h')
    expect(formatLength('under a minute')).toBe('under a minute')
  })
})

describe('reader error copy', () => {
  it('uses the busy copy for 429 and offers retry', () => {
    const e = new ApiError(429, { code: 'rate_limited', message: "The reader's AI tools are busy" })
    expect(readerErrorText(e)).toEqual({ title: BUSY_TEXT, detail: null, retry: true })
  })

  it('uses the paid copy for a 403 on a paid engine', () => {
    const e = new ApiError(403, { code: 'forbidden', message: 'Not allowed.' })
    expect(readerErrorText(e, { paidEngine: true }).title).toBe(PAID_TEXT)
    expect(readerErrorText(e, { paidEngine: false }).title).toBe(FORBIDDEN_TEXT)
  })

  it('shows safe validation text and hides paths', () => {
    const ok = new ApiError(400, { code: 'validation_error', message: 'No deepseek key is configured. Set one in Settings first.' })
    expect(readerErrorText(ok).detail).toBe('No deepseek key is configured. Set one in Settings first.')
    const leak = new ApiError(400, { code: 'validation_error', message: 'bad file /home/kae/library/x.db' })
    expect(readerErrorText(leak).detail).toBeNull()
    expect(readerErrorText(leak).retry).toBe(false)
  })
})
