import { describe, expect, it } from 'vitest'

import * as tr from './threeRun'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify({}), { status: 200 })
  }) as typeof fetch
}

describe('three-run api', () => {
  it('begins over the ticked lines, or every line when none', async () => {
    const calls: Call[] = []
    await tr.beginThreeRun(3, [1, 2], fakeFetch(calls))
    await tr.beginThreeRun(3, null, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/three-run/dramas/3/begin')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ line_ids: [1, 2] })
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({})
  })

  it('records a run, picks, keeps one run for the rest and closes', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await tr.captureThreeRun(3, 2, f)
    await tr.pickThreeRun(3, [{ line_id: 7, run: 1 }], f)
    await tr.keepRestThreeRun(3, 3, f)
    await tr.endThreeRun(3, f).catch(() => undefined)
    expect(calls.map((c) => `${c.init?.method ?? 'GET'} ${c.url}`)).toEqual([
      'POST /api/three-run/dramas/3/capture',
      'POST /api/three-run/dramas/3/pick',
      'POST /api/three-run/dramas/3/keep-rest',
      'DELETE /api/three-run/dramas/3',
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ picks: [{ line_id: 7, run: 1 }] })
  })
})
