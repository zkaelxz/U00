import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { createDrama, deleteDrama, getStats, searchLines } from './library'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

describe('library api', () => {
  it('encodes the search query', async () => {
    const { mock, f } = reply(200, { count: 0, items: [] })
    await searchLines('a b&c', f)
    expect(mock.mock.calls[0][0]).toBe('/api/library/search?q=a%20b%26c')
  })

  it('deletes with both confirmation params', async () => {
    const { mock, f } = reply(200, { deleted: true, drama_id: 4 })
    await deleteDrama(4, f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/dramas/4?confirm=true&confirm_text=DELETE')
    expect(init.method).toBe('DELETE')
  })

  it('posts the create body as JSON', async () => {
    const { mock, f } = reply(201, { id: 9 })
    await createDrama({ source_language: 'ja', title_en: 'X' }, f)
    const [, init] = mock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ source_language: 'ja', title_en: 'X' })
  })

  it('surfaces a 409 as ApiError', async () => {
    const { f } = reply(409, { error: { code: 'conflict', message: 'job running' } })
    await expect(deleteDrama(1, f)).rejects.toMatchObject({ status: 409 })
    await expect(getStats(f)).rejects.toBeInstanceOf(ApiError)
  })
})
