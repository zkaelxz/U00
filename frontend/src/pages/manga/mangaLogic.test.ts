import { describe, expect, it } from 'vitest'

import {
  MANGA_DEFAULTS, chapterCount, loadLastRead, loadMangaPrefs, mangaReadHref, mangaSeriesHref, pageInfos,
  saveLastRead, saveMangaPrefs,
} from './mangaLogic'

function memory() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  }
}

describe('saved manga logic', () => {
  it('remembers where the reader left off, per series', () => {
    const s = memory()
    expect(loadLastRead(s, 'MangaK', 'A')).toBeNull()
    saveLastRead(s, 'MangaK', 'A', { chapter: '0002 Ch 2', page: 5 })
    expect(loadLastRead(s, 'MangaK', 'A')).toEqual({ chapter: '0002 Ch 2', page: 5 })
    expect(loadLastRead(s, 'MangaK', 'B')).toBeNull()
    expect(loadLastRead(null, 'MangaK', 'A')).toBeNull()
  })

  it('ignores a damaged last-read entry', () => {
    const s = memory()
    for (const bad of ['{"chapter":"x","page":0}', '{"chapter":"","page":2}', '{"chapter":"x","page":1.5}', 'nope']) {
      s.setItem('baihe.pref.manga.last.MangaK/A', bad)
      expect(loadLastRead(s, 'MangaK', 'A')).toBeNull()
    }
  })

  it('view settings default to vertical scroll and never turn on typeset or text', () => {
    const s = memory()
    expect(loadMangaPrefs(s, 'MangaK', 'A')).toEqual(MANGA_DEFAULTS)
    saveMangaPrefs(s, 'MangaK', 'A', { mode: 'paged', rtl: true, fit: 'height', typeset: true, text: true })
    expect(loadMangaPrefs(s, 'MangaK', 'A')).toEqual({ mode: 'paged', rtl: true, fit: 'height', typeset: false, text: false })
    expect(loadMangaPrefs(s, 'MangaK', 'B')).toEqual(MANGA_DEFAULTS)
  })

  it('links encode every name', () => {
    expect(mangaSeriesHref('MangaK', 'Test Camp')).toBe('#/manga/MangaK/Test%20Camp')
    expect(mangaReadHref('MangaK', 'A#B', '0001 Ch 1', 3)).toBe('#/manga/MangaK/A%23B/0001%20Ch%201?page=3')
    expect(mangaReadHref('MangaK', 'A', 'c')).toBe('#/manga/MangaK/A/c')
  })

  it('maps saved pages to the comic viewer page shape', () => {
    expect(pageInfos([{ page: 1, width: 800, height: 1200 }, { page: 2, width: null, height: null }])).toEqual([
      { id: 1, ordinal: 1, width: 800, height: 1200, has_rendered: false, has_regions: false, image_version: null },
      { id: 2, ordinal: 2, width: null, height: null, has_rendered: false, has_regions: false, image_version: null },
    ])
    expect(chapterCount(1)).toBe('1 chapter')
    expect(chapterCount(3)).toBe('3 chapters')
  })
})
