import { describe, expect, it } from 'vitest'

import { getSpendHistory, money, monthLabel } from './spendHistory'

describe('spend history API', () => {
  it('asks for a month by query and encodes it', async () => {
    const urls: string[] = []
    const f = (async (input: RequestInfo | URL) => {
      urls.push(String(input))
      return new Response('{}', { status: 200 })
    }) as typeof fetch
    await getSpendHistory(undefined, f)
    await getSpendHistory('2026-09', f)
    expect(urls).toEqual(['/api/settings/spend-history', '/api/settings/spend-history?month=2026-09'])
  })

  it('labels months without a time zone shift', () => {
    expect(monthLabel('2026-10')).toBe('Oct 2026')
    expect(monthLabel('2026-01')).toBe('Jan 2026')
    expect(monthLabel('weird')).toBe('weird')
  })

  it('shows tiny amounts with more digits', () => {
    expect(money(12.345)).toBe('$12.35')
    expect(money(0)).toBe('$0.00')
    expect(money(0.004)).toBe('$0.0040')
  })
})
