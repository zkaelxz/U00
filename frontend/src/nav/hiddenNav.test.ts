import { describe, expect, it } from 'vitest'

import type { SessionState } from '../hooks/useSession'
import { hiddenNavKey, sanitizeHidden } from './hiddenNav'
import { customizableNavItems, visibleNavItemsFor, visibleRailGroups, isNavLocked, navId, type NavContext } from './navItems'

const MEMBER = ['library.read', 'lines.read', 'review.use']
const sessionOf = (id: number | null, permissions: string[]): SessionState => ({
  status: 'ready',
  me: {
    auth_enabled: id !== null,
    signed_in: true,
    sign_in_configured: true,
    zone: 'internet',
    user: { id, email: null, display_name: null, is_admin: false, is_local_owner: id === null },
    permissions,
  },
})
const ctx = (hidden: string[], permissions = MEMBER): NavContext => ({ session: sessionOf(7, permissions), pcMode: 'remote', developerMode: false, hidden })
const ids = (c: NavContext) => visibleNavItemsFor(c).map(navId)
const railIds = (c: NavContext) => visibleRailGroups(c).flatMap((g) => g.items.map((r) => navId(r.item)))

describe('customize menu registry', () => {
  it('lists nothing hidden when the person hid nothing', () => {
    expect(ids(ctx([]))).toEqual(customizableNavItems(ctx([])).map(navId))
  })

  it('hides chosen items from every surface', () => {
    const c = ctx(['discover'])
    expect(ids(c)).not.toContain('discover')
    expect(railIds(c)).not.toContain('discover')
    expect(railIds(c)).toContain('sources')
  })

  it('keeps Library and Settings whatever is stored', () => {
    const c = ctx(['library', 'settings', 'live'])
    expect(ids(c)).toEqual(expect.arrayContaining(['library', 'settings']))
    expect(ids(c)).not.toContain('live')
    const locked = customizableNavItems(c).filter(isNavLocked).map(navId)
    expect(locked).toEqual(['library', 'settings'])
  })

  it('does not list items the person lacks permission or mode for', () => {
    const listed = customizableNavItems(ctx([])).map(navId)
    expect(listed).not.toContain('admin')
    expect(listed).not.toContain('diagnostics')
    expect(listed).not.toContain('assistant')
    expect(customizableNavItems(ctx([], [...MEMBER, 'admin.users.read'])).map(navId)).toContain('admin')
  })

  it('ignores ids that match no item', () => {
    expect(ids(ctx(['nope']))).toEqual(ids(ctx([])))
  })
})

describe('stored choice', () => {
  it('drops unknown, locked, duplicate and non-string entries', () => {
    expect(sanitizeHidden(['discover', 'discover', 'library', 'settings', 'nope', 3, null])).toEqual(['discover'])
    expect(sanitizeHidden('discover')).toEqual([])
    expect(sanitizeHidden(undefined)).toEqual([])
  })

  it('is kept per person; the PC owner without an id has one of their own', () => {
    expect(hiddenNavKey(sessionOf(7, []))).toBe('nav.hidden.7')
    expect(hiddenNavKey(sessionOf(8, []))).toBe('nav.hidden.8')
    expect(hiddenNavKey(sessionOf(null, []))).toBe('nav.hidden.owner')
    expect(hiddenNavKey({ status: 'unavailable' })).toBe('nav.hidden.owner')
  })
})
