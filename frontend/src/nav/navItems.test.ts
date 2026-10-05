import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../api/auth'
import type { SessionState } from '../hooks/useSession'
import { NAV_ITEMS, visibleNavItems, type NavContext } from './navItems'

const owner = { id: 1, email: 'o@example.com', display_name: 'Owner', is_admin: true, is_local_owner: true }
const meOf = (over: Partial<AuthMe>): SessionState => ({
  status: 'ready',
  me: { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user: owner, permissions: [], ...over },
})

const MEMBER_PERMISSIONS = ['library.read', 'lines.read', 'lines.edit', 'review.use', 'jobs.start', 'jobs.cancel']

const personas: Record<string, NavContext> = {
  'owner at the PC': {
    session: meOf({ auth_enabled: false, zone: 'local', permissions: [...MEMBER_PERMISSIONS, 'admin.users.read', 'admin.users', 'admin.diagnostics', 'admin.settings', 'admin.library'] }),
    pcMode: 'local',
    developerMode: false,
  },
  'household member': {
    session: meOf({ user: { ...owner, is_admin: false, is_local_owner: false }, permissions: MEMBER_PERMISSIONS }),
    pcMode: 'remote',
    developerMode: false,
  },
  'remote admin': {
    session: meOf({ user: { ...owner, is_local_owner: false }, permissions: [...MEMBER_PERMISSIONS, 'admin.users.read', 'admin.diagnostics', 'admin.settings'] }),
    pcMode: 'remote',
    developerMode: false,
  },
}

const labels = (surface: 'header' | 'gear', ctx: NavContext) => visibleNavItems(surface, ctx).map((i) => i.label)

const HEADER = ['Library', 'Quick translate', 'Sources', 'Discover', 'Live']

describe('nav registry shows what the header and gear showed before it existed', () => {
  it('owner at the PC', () => {
    expect(labels('header', personas['owner at the PC'])).toEqual(HEADER)
    expect(labels('gear', personas['owner at the PC'])).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('household member: no Admin; Diagnostics still listed (its page refuses)', () => {
    expect(labels('header', personas['household member'])).toEqual(HEADER)
    expect(labels('gear', personas['household member'])).toEqual(['Jobs', 'Settings', 'Diagnostics'])
  })

  it('remote admin: Admin and Diagnostics, no Assistant', () => {
    expect(labels('header', personas['remote admin'])).toEqual(HEADER)
    expect(labels('gear', personas['remote admin'])).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('auth unavailable: renders as before sign-in existed, Admin included', () => {
    const ctx: NavContext = { session: { status: 'unavailable' }, pcMode: 'local', developerMode: false }
    expect(labels('gear', ctx)).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('session still loading: no Admin', () => {
    const ctx: NavContext = { session: { status: 'loading' }, pcMode: 'unknown', developerMode: false }
    expect(labels('gear', ctx)).toEqual(['Jobs', 'Settings', 'Diagnostics'])
  })
})

describe('Assistant', () => {
  const base = personas['owner at the PC']

  it('needs Developer Mode on the PC', () => {
    expect(labels('gear', { ...base, developerMode: true })).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics', 'Assistant'])
  })

  it('waits for /api/meta: hidden while the PC check is unknown or remote', () => {
    expect(labels('gear', { ...base, pcMode: 'unknown', developerMode: true })).not.toContain('Assistant')
    expect(labels('gear', { ...base, pcMode: 'remote', developerMode: true })).not.toContain('Assistant')
  })
})

describe('registry data', () => {
  it('has unique labels (the menus key on them)', () => {
    const l = NAV_ITEMS.map((i) => i.label)
    expect(new Set(l).size).toBe(l.length)
  })

  it('records the permissions D6 asks about without hiding by them', () => {
    const by = Object.fromEntries(NAV_ITEMS.map((i) => [i.label, i]))
    expect(by['Diagnostics'].requires).toBe('admin.diagnostics')
    expect(by['Benchmark Lab'].requires).toBe('admin.diagnostics')
    expect(by['Diagnostics'].hideWithoutPermission).toBe(false)
    expect(by['Benchmark Lab'].surface).toBe('none')
  })
})
