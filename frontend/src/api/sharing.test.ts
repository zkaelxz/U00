import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { getShareByDefault, listSharing, setItemPrivate, setShareByDefault } from './sharing'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

describe('sharing api', () => {
  it('lists one page of items', async () => {
    const r = reply(200, { total: 0, offset: 20, limit: 10, items: [] })
    expect((await listSharing(20, 10, r.f)).offset).toBe(20)
    expect(r.mock.mock.calls[0][0]).toBe('/api/sharing/items?offset=20&limit=10')
  })

  it('flips a title or a series by its own path', async () => {
    const d = reply(200, { kind: 'drama', id: 3, is_private: true })
    await setItemPrivate('drama', 3, true, d.f)
    const [url, init] = d.mock.mock.calls[0]
    expect(url).toBe('/api/sharing/dramas/3/private')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ private: true })

    const s = reply(200, { kind: 'series', id: 4, is_private: false })
    await setItemPrivate('series', 4, false, s.f)
    expect(s.mock.mock.calls[0][0]).toBe('/api/sharing/series/4/private')
    expect(JSON.parse(s.mock.mock.calls[0][1].body)).toEqual({ private: false })
  })

  it("passes the server's 409 message through", async () => {
    const r = reply(409, { error: { code: 'conflict', message: 'Make the whole series private instead' } })
    const err = await setItemPrivate('drama', 1, true, r.f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
    expect((err as ApiError).message).toBe('Make the whole series private instead')
  })

  it('reads and sets share-by-default', async () => {
    const g = reply(200, { share_by_default: false })
    expect(await getShareByDefault(g.f)).toEqual({ share_by_default: false })
    expect(g.mock.mock.calls[0][0]).toBe('/api/sharing/share-by-default')

    const p = reply(200, { share_by_default: true })
    await setShareByDefault(true, p.f)
    const [url, init] = p.mock.mock.calls[0]
    expect(url).toBe('/api/sharing/share-by-default')
    expect(JSON.parse(init.body)).toEqual({ share_by_default: true })
  })
})
