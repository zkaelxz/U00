import { describe, expect, it } from 'vitest'

import { applyUsageRecost, getUsageRecost, recostSummary, undoUsageRecost, usd, type UsageRecostPreview } from './usageRecost'

function fakeFetch(body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status: 200 })
  }) as typeof fetch
}

const PREVIEW: UsageRecostPreview = {
  rows: 2,
  models: [{ model: 'claude-sonnet-5-5', rows: 2, stored_usd: 40, recomputed_usd: 8 }],
  stored_usd: 40,
  recomputed_usd: 8,
  difference_usd: 32,
  month_stored_usd: 50,
  month_recomputed_usd: 18,
  recosted_rows: 0,
}

describe('usage re-cost API', () => {
  it('reads the preview, then applies with confirm and undoes', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    expect(await getUsageRecost(fakeFetch(PREVIEW, calls))).toEqual(PREVIEW)
    await applyUsageRecost(fakeFetch({ rows: 2, month_spend_usd: 18 }, calls))
    await undoUsageRecost(fakeFetch({ rows: 2, month_spend_usd: 50 }, calls))
    expect(calls.map((c) => c.url)).toEqual([
      '/api/settings/usage-recost',
      '/api/settings/usage-recost/apply',
      '/api/settings/usage-recost/undo',
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ confirm: true })
  })
})

describe('recostSummary', () => {
  it('says "about" and gives before and after', () => {
    expect(usd(1.005)).toMatch(/^\$1\.0/)
    const s = recostSummary(PREVIEW)
    expect(s).toContain('about $8.00')
    expect(s).toContain('$50.00 to about $18.00')
  })
  it('says so when nothing needs changing', () => {
    expect(recostSummary({ ...PREVIEW, rows: 0 })).toMatch(/Nothing to change/)
  })
})
