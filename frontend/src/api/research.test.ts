import { describe, expect, it, vi } from 'vitest'

import { getResearchProvenance } from './research'

describe('getResearchProvenance', () => {
  it('reads the title provenance route', async () => {
    const body = { drama_id: 7, fields: [] }
    const mock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(body), { status: 200 }))
    await expect(getResearchProvenance(7, mock as unknown as typeof fetch)).resolves.toEqual(body)
    expect(mock.mock.calls[0][0]).toBe('/api/metadata/dramas/7/provenance')
  })

  it('rejects on an error status', async () => {
    const mock = vi.fn().mockImplementation(async () => new Response('{"detail":"x"}', { status: 404 }))
    await expect(getResearchProvenance(7, mock as unknown as typeof fetch)).rejects.toBeTruthy()
  })
})
