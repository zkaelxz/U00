import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { AuthMe } from '../../api/auth'
import type { SharingItem } from '../../types/sharing'
import {
  applyFlip,
  applyOwner,
  canChangeOwners,
  canSeeAllItems,
  filterPcPrivate,
  isPcPrivate,
  followsSeries,
  groupBySharing,
  itemTitle,
  mergePage,
  seriesNote,
  shareDefaultHelp,
  showOwnerPicker,
  sharingErrorText,
  sharingStatusLabel,
} from './sharing'

const item = (over: Partial<SharingItem>): SharingItem => ({
  kind: 'drama',
  id: 1,
  title: 'T',
  owner_name: 'Ann',
  owner_user_id: 2,
  created_at_pc: false,
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
  it('shows every item only to admins; an unknown session gets the least-privilege view', () => {
    expect(canSeeAllItems({ status: 'loading' })).toBeNull()
    expect(canSeeAllItems({ status: 'unavailable' })).toBe(false)
    // admin.users.read: the household listener keeps it, so an admin away sees the list too.
    expect(canSeeAllItems({ status: 'ready', me: me(['library.read', 'admin.users.read']) })).toBe(true)
    expect(canSeeAllItems({ status: 'ready', me: me(['library.read', 'admin.library']) })).toBe(false)
    expect(canSeeAllItems({ status: 'ready', me: me(['library.read', 'lines.edit']) })).toBe(false)
  })

  it('offers the owner picker to admins and moves the row to its new owner', () => {
    expect(canChangeOwners({ status: 'ready', me: me(['admin.users.read', 'admin.users']) })).toBe(true)
    expect(canChangeOwners({ status: 'ready', me: me(['admin.users.read']) })).toBe(false)
    expect(canChangeOwners({ status: 'loading' })).toBe(false)
    const rows = [item({ id: 1 }), item({ id: 2 })]
    const toPc = applyOwner(rows, { kind: 'drama', id: 1, owner_user_id: null, owner_name: 'PC owner' })
    expect(toPc[0]).toMatchObject({ owner_user_id: null, owner_name: 'PC owner', created_at_pc: true })
    expect(toPc[1]).toEqual(rows[1])
    const toBo = applyOwner(toPc, { kind: 'drama', id: 1, owner_user_id: 3, owner_name: 'Bo' })
    expect(toBo[0]).toMatchObject({ owner_user_id: 3, owner_name: 'Bo', created_at_pc: false })
  })

  it('a title in a series follows the series', () => {
    const solo = item({ is_private: true })
    const inSeries = item({ series_id: 7, series_name: 'Saga', series_is_private: false, is_private: false })
    expect(followsSeries(solo)).toBe(false)
    expect(sharingStatusLabel(solo)).toBe('Private')
    expect(followsSeries(inSeries)).toBe(true)
    expect(sharingStatusLabel(inSeries)).toBe('Shared')
    expect(sharingStatusLabel({ ...inSeries, series_is_private: true })).toBe('Private')
    expect(seriesNote(inSeries)).toContain('“Saga”')
    expect(sharingStatusLabel(item({ kind: 'series', is_private: false }))).toBe('Shared')
  })

  it('finds private items created at the PC, keeping their series heading', () => {
    const items = [
      item({ id: 1, created_at_pc: true, is_private: true }),
      item({ id: 2, created_at_pc: true, is_private: false }),
      item({ id: 3, is_private: true }),
      item({ kind: 'series', id: 7, created_at_pc: false }),
      item({ id: 4, created_at_pc: true, series_id: 7, series_is_private: true }),
      item({ kind: 'series', id: 8, created_at_pc: true, is_private: true }),
      item({ id: 5, created_at_pc: true, series_id: 8, series_is_private: false }),
    ]
    expect(isPcPrivate(items[0])).toBe(true)
    expect(isPcPrivate(items[1])).toBe(false)
    expect(isPcPrivate(items[2])).toBe(false)
    expect(filterPcPrivate(items).map((i) => `${i.kind}:${i.id}`)).toEqual(['drama:1', 'series:7', 'drama:4', 'series:8'])
  })

  it('names untitled items plainly', () => {
    expect(itemTitle(item({ id: 4, title: ' ' }))).toBe('#4')
    expect(itemTitle(item({ kind: 'series', title: '' }))).toBe('Untitled series')
  })

  it('a series flip updates the series and its titles only', () => {
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
    expect(sharingErrorText(new ApiError(0, { code: 'network_error', message: 'Could not reach the Baihe API. Is it running?' })))
      .toMatch(/^Could not reach Baihe\./)
    expect(sharingErrorText(new ApiError(500, { code: 'internal_error', message: 'KeyError: owner_user_id' }))).not.toMatch(/KeyError/)
    expect(shareDefaultHelp(false, false)).toMatch(/^Off: .*private.*only affects new items/)
    expect(shareDefaultHelp(true, true)).toMatch(/^On: .*list below/)
  })

  it('groups shared items before private ones, keeping a series and its titles together in list order', () => {
    const series = item({ kind: 'series', id: 9, is_private: true })
    const ep = item({ id: 4, series_id: 9, series_is_private: true, is_private: false })
    const open = item({ id: 2, is_private: false })
    const hidden = item({ id: 3, is_private: true })
    const groups = groupBySharing([hidden, series, ep, open])
    expect(groups.map((g) => g.label)).toEqual(['Shared', 'Private'])
    expect(groups[0].items).toEqual([open])
    expect(groups[1].items).toEqual([hidden, series, ep])
    expect(groupBySharing([open]).map((g) => g.label)).toEqual(['Shared'])
    expect(groupBySharing([])).toEqual([])
  })

  it('offers the owner picker only when someone besides the PC owner can be chosen', () => {
    const pcItem = item({ owner_user_id: null, owner_name: 'PC owner', created_at_pc: true })
    expect(showOwnerPicker(pcItem, [])).toBe(false)
    expect(showOwnerPicker(pcItem, [{ id: 2, name: 'Ann' }])).toBe(true)
    // An item already owned by an account can still be handed back to the PC.
    expect(showOwnerPicker(item({ owner_user_id: 2 }), [])).toBe(true)
  })
})
