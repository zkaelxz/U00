import { describe, expect, it } from 'vitest'

import type { StorageLike } from '../../components/sectionStorage'
import {
  clampPage,
  clampZoom,
  comicHref,
  hashIsComic,
  defaultPrefs,
  isComicType,
  keyAction,
  loadComicPrefs,
  NO_ZOOM,
  orderedLines,
  preloadWindow,
  regionBox,
  sanitizePrefs,
  saveComicPrefs,
  tapAction,
  textPages,
  toggleZoomAt,
  zoomAt,
} from './comicLogic'

function memoryStorage(): StorageLike & { data: Record<string, string> } {
  const data: Record<string, string> = {}
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
  }
}

describe('comic types and defaults', () => {
  it('knows which media types are comics', () => {
    for (const t of ['manga', 'manhua', 'manhwa', 'Manhwa']) expect(isComicType(t)).toBe(true)
    for (const t of ['novel', 'audio_drama', '', null, undefined]) expect(isComicType(t)).toBe(false)
  })

  it('defaults manga to paged right-to-left and manhua/manhwa to vertical', () => {
    expect(defaultPrefs('manga')).toEqual({ mode: 'paged', rtl: true, fit: 'height', typeset: true, text: false })
    expect(defaultPrefs('manhua')).toMatchObject({ mode: 'vertical', rtl: false, fit: 'width' })
    expect(defaultPrefs('manhwa')).toMatchObject({ mode: 'vertical', rtl: false, fit: 'width' })
    expect(defaultPrefs(null)).toMatchObject({ mode: 'vertical', rtl: false })
  })

  it('lets the server default choose the mode', () => {
    expect(defaultPrefs('manhwa', 'paged')).toMatchObject({ mode: 'paged', fit: 'height', rtl: false })
    expect(defaultPrefs('manga', 'vertical')).toMatchObject({ mode: 'vertical', fit: 'width', rtl: true })
    expect(defaultPrefs('manga', 'sideways')).toMatchObject({ mode: 'paged' })
  })

  it('sanitises stored prefs over the defaults', () => {
    const d = defaultPrefs('manga')
    expect(sanitizePrefs(null, d)).toEqual(d)
    expect(sanitizePrefs({ mode: 'vertical', rtl: 'yes', fit: 'huge', text: true }, d)).toEqual({ ...d, mode: 'vertical', text: true })
  })

  it('remembers prefs per title', () => {
    const s = memoryStorage()
    const d = defaultPrefs('manhua')
    expect(saveComicPrefs(s, 7, { ...d, mode: 'paged' })).toBe(true)
    expect(Object.keys(s.data)).toEqual(['baihe.pref.comic.view.7'])
    expect(loadComicPrefs(s, 7, d).mode).toBe('paged')
    expect(loadComicPrefs(s, 8, d).mode).toBe('vertical')
    expect(loadComicPrefs(null, 7, d)).toEqual(d)
  })
})

describe('paging', () => {
  it('builds the route and clamps pages', () => {
    expect(comicHref(3, 2)).toBe('#/comic/3?page=2')
    expect(comicHref(3, null)).toBe('#/comic/3')
    expect(hashIsComic('#/comic/3?page=2', 3)).toBe(true)
    expect(hashIsComic('#/comic/3', 3)).toBe(true)
    expect(hashIsComic('#/comic/4', 3)).toBe(false)
    expect(hashIsComic('#/library', 3)).toBe(false)
    expect(clampPage(0, 10)).toBe(1)
    expect(clampPage(11, 10)).toBe(10)
    expect(clampPage(4.4, 10)).toBe(4)
    expect(clampPage(Number.NaN, 10)).toBe(1)
    expect(clampPage(3, 0)).toBe(1)
  })

  it('maps keys, flipping left and right for right-to-left paging', () => {
    expect(keyAction('arrowright', 'paged', false)).toBe('next')
    expect(keyAction('arrowleft', 'paged', false)).toBe('prev')
    expect(keyAction('arrowleft', 'paged', true)).toBe('next')
    expect(keyAction('arrowright', 'paged', true)).toBe('prev')
    for (const k of ['arrowdown', 'pagedown', ' ']) expect(keyAction(k, 'paged', true)).toBe('next')
    for (const k of ['arrowup', 'pageup', 'shift+ ']) expect(keyAction(k, 'paged', true)).toBe('prev')
    expect(keyAction('home', 'paged', true)).toBe('first')
    expect(keyAction('end', 'vertical', false)).toBe('last')
    expect(keyAction('+', 'paged', false)).toBe('zoomIn')
    expect(keyAction('-', 'vertical', false)).toBe('zoomOut')
    expect(keyAction('0', 'vertical', false)).toBe('zoomReset')
    expect(keyAction('j', 'paged', false)).toBeNull()
  })

  it('leaves scrolling keys to the browser in vertical mode, and ignores right-to-left there', () => {
    for (const k of ['arrowdown', 'arrowup', 'pagedown', 'pageup', ' ']) expect(keyAction(k, 'vertical', false)).toBeNull()
    expect(keyAction('arrowleft', 'vertical', true)).toBe('prev')
    expect(keyAction('arrowright', 'vertical', true)).toBe('next')
  })

  it('maps tap thirds, flipped for right-to-left', () => {
    expect(tapAction(0.1, false)).toBe('prev')
    expect(tapAction(0.9, false)).toBe('next')
    expect(tapAction(0.5, false)).toBe('chrome')
    expect(tapAction(0.1, true)).toBe('next')
    expect(tapAction(0.9, true)).toBe('prev')
    expect(tapAction(0.5, true)).toBe('chrome')
  })

  it('preloads the next three when scrolling and next plus previous when paged', () => {
    expect(preloadWindow(1, 10, 'vertical')).toEqual([2, 3, 4])
    expect(preloadWindow(9, 10, 'vertical')).toEqual([10])
    expect(preloadWindow(10, 10, 'vertical')).toEqual([])
    expect(preloadWindow(5, 10, 'paged')).toEqual([6, 4])
    expect(preloadWindow(1, 10, 'paged')).toEqual([2])
    expect(preloadWindow(1, 1, 'paged')).toEqual([])
  })

  it('fetches text for the page in view (and its neighbours when scrolling)', () => {
    expect(textPages(3, 10, 'paged')).toEqual([3])
    expect(textPages(3, 10, 'vertical')).toEqual([3, 4, 2])
    expect(textPages(1, 1, 'vertical')).toEqual([1])
  })
})

describe('text overlay', () => {
  it('positions boxes as percentages of the page', () => {
    expect(regionBox({ x: 100, y: 300, w: 200, h: 150 }, 800, 1200)).toEqual({ left: 12.5, top: 25, width: 25, height: 12.5 })
    expect(regionBox({ x: 0, y: 0, w: 1, h: 3 }, 3, 9)).toEqual({ left: 0, top: 0, width: 33.33, height: 33.33 })
  })

  it('clamps boxes to the page and drops empty ones', () => {
    expect(regionBox({ x: -50, y: 1100, w: 200, h: 300 }, 800, 1200)).toEqual({ left: 0, top: 91.67, width: 18.75, height: 8.33 })
    expect(regionBox({ x: 900, y: 0, w: 10, h: 10 }, 800, 1200)).toBeNull()
    expect(regionBox({ x: 10, y: 10, w: 0, h: 10 }, 800, 1200)).toBeNull()
    expect(regionBox({ x: 10, y: 10, w: 10, h: 10 }, null, 1200)).toBeNull()
    expect(regionBox({ x: 10, y: 10, w: 10, h: 10 }, 0, 0)).toBeNull()
  })

  it('lists lines in reading order, falling back to the source text', () => {
    const r = (idx: number, translated_text: string | null, source_text: string | null) => ({ idx, x: 0, y: 0, w: 1, h: 1, translated_text, source_text, kind: 'bubble' })
    expect(orderedLines([r(2, 'Two', '二'), r(0, '', '零'), r(1, '  ', null)]).map((l) => [l.idx, l.text])).toEqual([
      [0, '零'],
      [2, 'Two'],
    ])
  })
})

describe('zoom maths', () => {
  it('zooms around a point and keeps it still', () => {
    const z = zoomAt(NO_ZOOM, 2, 100, 50, 400, 600)
    expect(z).toEqual({ scale: 2, x: -100, y: -50 })
    // The point under (100, 50) is still content point (100, 50).
    expect((100 - z.x) / z.scale).toBe(100)
  })

  it('keeps the zoomed image covering its box', () => {
    expect(zoomAt(NO_ZOOM, 2, 0, 0, 400, 600)).toEqual({ scale: 2, x: 0, y: 0 })
    expect(zoomAt(NO_ZOOM, 2, 400, 600, 400, 600)).toEqual({ scale: 2, x: -400, y: -600 })
    expect(clampZoom({ scale: 2, x: 50, y: -9999 }, 400, 600)).toEqual({ scale: 2, x: 0, y: -600 })
    expect(clampZoom({ scale: 9, x: 0, y: 0 }, 400, 600).scale).toBe(4)
    expect(clampZoom({ scale: 0.5, x: -10, y: -10 }, 400, 600)).toEqual(NO_ZOOM)
  })

  it('double-tap toggles between 2x and fit', () => {
    const z = toggleZoomAt(NO_ZOOM, 200, 300, 400, 600)
    expect(z.scale).toBe(2)
    expect(toggleZoomAt(z, 10, 10, 400, 600)).toEqual(NO_ZOOM)
  })
})
