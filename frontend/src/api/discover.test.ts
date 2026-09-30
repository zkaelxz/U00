import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  bulkCommit,
  deleteTitle,
  importSuggestion,
  listPlatforms,
  listTitles,
  queryString,
  searchLinks,
  startBulkExtract,
  startNavigationHelp,
  translateQuery,
} from './discover'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}
const sent = (mock: ReturnType<typeof vi.fn>, i = 0) => JSON.parse(mock.mock.calls[i][1].body)

afterEach(() => resetPcModeForTests())

describe('discover api', () => {
  it('puts only set filters in the query string', async () => {
    expect(queryString({ q: '', language: 'zh', media_type: undefined })).toBe('?language=zh')
    const { mock, f } = reply(200, { titles: [], total: 0 })
    await listTitles({ q: '百合 x' }, f)
    await listTitles({}, f)
    expect(mock.mock.calls[0][0]).toBe('/api/discover/titles?q=%E7%99%BE%E5%90%88+x')
    expect(mock.mock.calls[1][0]).toBe('/api/discover/titles')
  })

  it('unwraps platforms and search links', async () => {
    const p = reply(200, { platforms: [{ name: 'A', url: 'https://a' }] })
    await expect(listPlatforms('zh', '', p.f)).resolves.toEqual([{ name: 'A', url: 'https://a' }])
    expect(p.mock.mock.calls[0][0]).toBe('/api/discover/platforms?language=zh')
    const l = reply(200, { links: [] })
    await expect(searchLinks('x', 'novel', 'baihe', '', l.f)).resolves.toEqual([])
    expect(l.mock.mock.calls[0][0]).toBe('/api/discover/search-links?q=x&format=novel')
    const a = reply(200, { links: [] })
    await searchLinks('x', '', 'any', '言情', a.f)
    expect(a.mock.mock.calls[0][0]).toBe('/api/discover/search-links?q=x&genre=any&tag=%E8%A8%80%E6%83%85')
    const b = reply(200, { links: [] })
    await searchLinks('x', '', 'baihe', 'ignored', b.f)
    expect(b.mock.mock.calls[0][0]).toBe('/api/discover/search-links?q=x')
  })

  it('sends the engine name only when one is picked, never a key', async () => {
    const { mock, f } = reply(200, { job_id: 'j', started: true })
    await translateQuery('moon', undefined, f)
    await translateQuery('moon', 'gemini', f)
    await importSuggestion('https://a.cn', 'claude', f)
    await startBulkExtract(['https://a.cn'], 'lbl', undefined, f)
    await startNavigationHelp({ url: 'https://a.cn', goal: 'g', target_language: 'English' }, 'ollama', f)
    expect(sent(mock, 0)).toEqual({ q: 'moon' })
    expect(sent(mock, 1)).toEqual({ q: 'moon', engine: 'gemini' })
    expect(sent(mock, 2)).toEqual({ url: 'https://a.cn', engine: 'claude' })
    expect(sent(mock, 3)).toEqual({ urls: ['https://a.cn'], source_label: 'lbl' })
    expect(sent(mock, 4)).toEqual({ url: 'https://a.cn', goal: 'g', target_language: 'English', engine: 'ollama' })
    expect(mock.mock.calls[4][0]).toBe('/api/discover/navigation-help')
  })

  it('bulk commit posts the entries and label', async () => {
    const { mock, f } = reply(200, { added: 1, skipped: 0, ids: [3] })
    const e = { title: 'A', author: '', tags: '', source_url: '', has_audio_drama: false }
    await bulkCommit([e], 'lbl', f)
    expect(mock.mock.calls[0][0]).toBe('/api/discover/bulk-commit')
    expect(sent(mock)).toEqual({ entries: [e], source_label: 'lbl' })
  })

  it('delete is PC-only: confirm, X-Baihe-Local, and a 403 marks the tab remote', async () => {
    const ok = reply(200, { deleted: true, id: 5 })
    await deleteTitle(5, ok.f)
    expect(ok.mock.mock.calls[0][0]).toBe('/api/discover/titles/5/delete')
    expect(sent(ok.mock)).toEqual({ confirm: true })
    expect(new Headers(ok.mock.mock.calls[0][1].headers).get('X-Baihe-Local')).toBe('1')
    const no = reply(403, { error: { code: 'forbidden', message: 'x' } })
    await expect(deleteTitle(5, no.f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
