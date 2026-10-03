import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  getSavedPages, getSaveFolder, listSavedChapters, listSavedSeries, openSaveFolder, savedPageUrl, setSaveFolder,
} from './savedComics'
import { setTrackedSave } from './sources'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('saved comics api', () => {
  it('folder calls are PC only', async () => {
    const { mock, f } = reply(200, { folder: 'D:\\Manga', custom: true, picked_missing: false })
    await getSaveFolder(f)
    await setSaveFolder('D:\\Manga', f)
    await openSaveFolder(f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/saved-comics/folder', '/api/saved-comics/folder', '/api/saved-comics/folder/open',
    ])
    expect(mock.mock.calls.every((c) => localHeader(c[1]) === '1')).toBe(true)
    expect(JSON.parse(mock.mock.calls[1][1].body as string)).toEqual({ folder: 'D:\\Manga' })
  })

  it('a refused folder call marks this tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'no' } })
    await expect(getSaveFolder(f)).rejects.toThrow()
    expect(getPcMode()).toBe('remote')
  })

  it('names go in the query, encoded', async () => {
    const { mock, f } = reply(200, [])
    await listSavedSeries(f)
    await listSavedChapters('MangaK', 'A & B', f)
    await getSavedPages('MangaK', 'A', '0001 #1?', f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/saved-comics/series',
      '/api/saved-comics/chapters?source=MangaK&series=A+%26+B',
      '/api/saved-comics/pages?source=MangaK&series=A&chapter=0001+%231%3F',
    ])
    expect(savedPageUrl('MangaK', 'A', 'c/1', 2)).toBe('/api/saved-comics/page?source=MangaK&series=A&chapter=c%2F1&page=2')
  })

  it('tracked save switch posts the flag', async () => {
    const { mock, f } = reply(200, [])
    await setTrackedSave('mangak', 's1', true, f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/tracked/save-cbz')
    expect(JSON.parse(mock.mock.calls[0][1].body as string)).toEqual({ source: 'mangak', series_id: 's1', save_cbz: true })
  })
})
