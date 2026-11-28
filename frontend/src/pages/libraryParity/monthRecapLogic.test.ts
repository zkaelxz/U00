import { describe, expect, it } from 'vitest'

import type { LibraryRecap } from '../../types/library'
import { isDismissed, recapItems, recapMonth, rememberDismissed } from './monthRecapLogic'

const EMPTY: LibraryRecap = {
  month: '2026-10', titles_read: 0, titles_finished: 0, chapters_read: 0, moments_saved: 0, active_days: 0,
  most_days: null, spend_usd: null,
}

function memoryStorage() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
}

describe('recapMonth', () => {
  it('names last month during the first seven days of a month', () => {
    expect(recapMonth(new Date(2026, 10, 1))).toEqual({ month: '2026-10', name: 'October' })
    expect(recapMonth(new Date(2026, 10, 7, 23, 59))).toEqual({ month: '2026-10', name: 'October' })
    expect(recapMonth(new Date(2027, 0, 3))).toEqual({ month: '2026-12', name: 'December' })
  })

  it('is gone from the eighth day on', () => {
    expect(recapMonth(new Date(2026, 10, 8))).toBeNull()
    expect(recapMonth(new Date(2026, 10, 30))).toBeNull()
  })
})

describe('dismissing', () => {
  it('is remembered per account and per month', () => {
    const s = memoryStorage()
    rememberDismissed(s, 7, '2026-10')
    expect(isDismissed(s, 7, '2026-10')).toBe(true)
    expect(isDismissed(s, 8, '2026-10')).toBe(false)
    expect(isDismissed(s, null, '2026-10')).toBe(false)
    expect(isDismissed(s, 7, '2026-11')).toBe(false)
  })

  it('shows the recap when storage is unavailable', () => {
    const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
    expect(rememberDismissed(broken, 7, '2026-10')).toBe(false)
    expect(isDismissed(broken, 7, '2026-10')).toBe(false)
    expect(isDismissed(null, 7, '2026-10')).toBe(false)
  })
})

describe('recapItems', () => {
  it('has nothing for an empty month', () => {
    expect(recapItems(EMPTY)).toEqual([])
  })

  it('leaves out the zeroes and says one or many', () => {
    expect(recapItems({ ...EMPTY, titles_read: 1, chapters_read: 12, active_days: 3 })).toEqual([
      { value: '1', label: 'title read' },
      { value: '12', label: 'chapters read' },
      { value: '3', label: 'days with Baihe' },
    ])
  })

  it('shows spend only when the server sent it', () => {
    expect(recapItems({ ...EMPTY, spend_usd: 1.5 })).toEqual([{ value: '$1.50', label: 'spent on this PC' }])
    expect(recapItems({ ...EMPTY, spend_usd: null })).toEqual([])
  })
})
