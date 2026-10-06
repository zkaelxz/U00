import { describe, expect, it, vi } from 'vitest'

import { getPeaks } from './media'

describe('getPeaks', () => {
  it('hands the abort signal to fetch so a superseded window is dropped', async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ start: 1, end: 2, buckets: 16, peaks: [] }), { status: 200 }))
    const ctl = new AbortController()
    await getPeaks(7, 1, 2, 16, ctl.signal, f as unknown as typeof fetch)
    expect((f.mock.calls[0] as unknown[])[0]).toContain('/api/media/dramas/7/peaks?start=1&end=2&buckets=16')
    expect((f.mock.calls[0] as unknown[])[1]).toMatchObject({ signal: ctl.signal })
  })
})
