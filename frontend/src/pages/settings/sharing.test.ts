import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { AuthMe } from '../../api/auth'
import type { SharingItem } from '../../types/sharing'
import {
  applyFlip,
  canSeeAllItems,
  followsSeries,
  itemTitle,
  mergePage,
  seriesNote,
  shareDefaultHelp,
  sharingErrorText,
  statusLabel,
} from './sharing'

const item = (over: Partial<SharingItem>): SharingItem => ({
  kind: 'drama',
  id: 1,
  title: 'T',
  owner_name: 'Ann',
  is_private: false,
  series_id: null,
  series_name: null,
  series_is_private: null,
  ...over,
})

const me = (permissions: string[]): AuthMe => ({
  auth_enabled: true,
  signed_in: true,
  sign_in_configured: true,
  zone: 'internet',
  user: null,
  permissions,
})

describe('sharing helpers', () => {
  it('shows every item only to admins (or an unknown session, which the server checks)', () => {
    expect(canSeeAllItems({ status: 'loading' })).toBeNull()
    expect(canSeeAllItems({ status: 'unavailable' })).toBe(true)
    expect(canSeeAllItems({ status: 'ready', me: me(['library.read', 'admin.library']) })).toBe(true)
    expect(canSeeAllItems({ status: 'ready', me: me(['library.read', 'lines.edit']) })).toBe(false)
  })

  it('a drama in a series follows the series', () => {
    const solo = item({ is_private: true })
    const inSeries = item({ series_id: 7, series_name: 'Saga', series_is_private: false, is_private: false })
    expect(followsSeries(solo)).toBe(false)
    expect(statusLabel(solo)).toBe('Private')
    expect(followsSeries(inSeries)).toBe(true)
    expect(statusLabel(inSeries)).toBe('Shared')
    expect(statusLabel({ ...inSeries, series_is_private: true })).toBe('Private')
    expect(seriesNote(inSeries)).toContain('“Saga”')
    expect(statusLabel(item({ kind: 'series', is_private: false }))).toBe('Shared')
  })

  it('names untitled items plainly', () => {
    expect(itemTitle(item({ title: ' ' }))).toBe('Untitled drama')
    expect(itemTitle(item({ kind: 'series', title: '' }))).toBe('Untitled series')
  })

  it('a series flip updates the series and its dramas only', () => {
    const items = [
      item({ kind: 'series', id: 7 }),
      item({ id: 1, series_id: 7, series_is_private: false }),
      item({ id: 7 }),
      item({ id: 2, series_id: 8, series_is_private: false }),
    ]
    const next = applyFlip(items, { kind: 'series', id: 7, is_private: true })
    expect(next.map((i) => [i.is_private, i.series_is_private])).toEqual([
      [true, null],
      [false, true],
      [false, null],
      [false, false],
    ])
    expect(applyFlip(items, { kind: 'drama', id: 7, is_private: true })[2].is_private).toBe(true)
  })

  it('appends a page without repeating an item', () => {
    const a = [item({ id: 1 }), item({ kind: 'series', id: 1 })]
    const merged = mergePage(a, [item({ id: 1 }), item({ id: 2 })])
    expect(merged.map((i) => `${i.kind}:${i.id}`)).toEqual(['drama:1', 'series:1', 'drama:2'])
  })

  it("shows the server's message, and says the setting only affects new items", () => {
    const conflict = new ApiError(409, { code: 'conflict', message: 'Make the whole series private instead' })
    expect(sharingErrorText(conflict)).toBe('Make the whole series private instead')
    expect(sharingErrorText(new ApiError(403, { code: 'forbidden', message: 'Not allowed.' }))).toMatch(/permission/)
    expect(sharingErrorText(new TypeError('x'))).toMatch(/connection/)
    expect(shareDefaultHelp(false, false)).toMatch(/^Off: .*private.*only affects new items/)
    expect(shareDefaultHelp(true, true)).toMatch(/^On: .*list below/)
  })
})
