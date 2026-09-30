import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AuthMe } from '../api/auth'
import { ApiError } from '../api/client'
import { resetSessionForTests } from '../hooks/useSession'
import { SharingControl } from './SharingControl'
import { flipSharing, sharingView } from './sharingControl'

const me = (over: Partial<AuthMe> = {}): AuthMe => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet',
  user: { id: 7, email: null, display_name: 'Kae', is_admin: false, is_local_owner: false },
  permissions: ['library.read', 'lines.edit'], ...over,
})
const ready = (m: AuthMe) => ({ status: 'ready' as const, me: m })

describe('sharingView', () => {
  it('shows nothing with sign-in off, signed out, unknown session, or an item that is not mine', () => {
    const item = { kind: 'drama' as const, is_private: true, owned_by_me: true }
    expect(sharingView(ready(me({ auth_enabled: false })), item).show).toBe(false)
    expect(sharingView(ready(me({ signed_in: false })), item).show).toBe(false)
    expect(sharingView({ status: 'loading' }, item).show).toBe(false)
    expect(sharingView({ status: 'unavailable' }, item).show).toBe(false)
    expect(sharingView(ready(me()), { ...item, owned_by_me: false }).show).toBe(false)
    expect(sharingView(ready(me()), { ...item, is_private: undefined }).show).toBe(false)
  })

  it('labels the state and the action', () => {
    expect(sharingView(ready(me()), { kind: 'drama', is_private: true, owned_by_me: true }))
      .toEqual({ show: true, label: 'Private', follows: false, actionLabel: 'Share with household' })
    expect(sharingView(ready(me()), { kind: 'series', is_private: false, owned_by_me: true }))
      .toEqual({ show: true, label: 'Shared', follows: false, actionLabel: 'Make private' })
  })

  it('marks a drama in a series as following it', () => {
    const v = sharingView(ready(me()), { kind: 'drama', is_private: true, owned_by_me: true, series_id: 4 })
    expect(v.show && v.follows).toBe(true)
  })
})

describe('flipSharing', () => {
  it('asks for the opposite of the current state and returns the confirmed one', async () => {
    const set = vi.fn().mockResolvedValue({ is_private: false })
    expect(await flipSharing(set, 'drama', 3, true)).toEqual({ isPrivate: false })
    expect(set).toHaveBeenCalledWith('drama', 3, false)
  })

  it("returns the server's own words on a 409", async () => {
    const set = vi.fn().mockRejectedValue(new ApiError(409, { code: 'conflict', message: 'Make the whole series private instead' }))
    expect(await flipSharing(set, 'series', 9, false)).toEqual({ error: 'Make the whole series private instead' })
  })

  it('says plainly when the server cannot be reached', async () => {
    const set = vi.fn().mockRejectedValue(new TypeError('failed'))
    expect(await flipSharing(set, 'series', 9, false)).toEqual({ error: "Couldn't reach Baihe. Check the connection and try again." })
  })
})

describe('SharingControl', () => {
  const html = (props: Record<string, unknown>) =>
    renderToStaticMarkup(createElement(SharingControl, { kind: 'drama', id: 3, title: 'Hidden', ...props }))

  beforeEach(() => resetSessionForTests(ready(me())))

  it('shows a badge and one button for my private drama', () => {
    const out = html({ isPrivate: true, ownedByMe: true })
    expect(out).toContain('>Private</span>')
    expect(out).toContain('aria-label="Share with household: Hidden"')
  })

  it('shows Shared and Make private for my shared series', () => {
    const out = html({ kind: 'series', isPrivate: false, ownedByMe: true, title: 'Saga' })
    expect(out).toContain('>Shared</span>')
    expect(out).toContain('aria-label="Make private: Saga"')
  })

  it('explains instead of offering a button for a drama in a series', () => {
    const out = html({ isPrivate: true, ownedByMe: true, seriesId: 9 })
    expect(out).not.toContain('<button')
    expect(out).toContain('whole series')
  })

  it('renders nothing when the item is not mine or sign-in is off', () => {
    expect(html({ isPrivate: true, ownedByMe: false })).toBe('')
    resetSessionForTests(ready(me({ auth_enabled: false })))
    expect(html({ isPrivate: true, ownedByMe: true })).toBe('')
  })
})
