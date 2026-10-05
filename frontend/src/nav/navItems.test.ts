import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../api/auth'
import type { SessionState } from '../hooks/useSession'
import type { Route } from '../router'
import { PREF_KEY_PREFIX, readPref, writePref } from '../hooks/usePersistedState'
import { NAV_ITEMS, RAIL_COLLAPSED_KEY, visibleNavItems, visibleRailGroups, type NavContext } from './navItems'

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
  'member with admin.diagnostics': {
    session: meOf({ user: { ...owner, is_admin: false, is_local_owner: false }, permissions: [...MEMBER_PERMISSIONS, 'admin.diagnostics'] }),
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

const HEADER = ['Library', 'Translate text', 'Sources', 'Discover', 'Live']

describe('nav registry shows what the header and gear showed before it existed', () => {
  it('owner at the PC', () => {
    expect(labels('header', personas['owner at the PC'])).toEqual(HEADER)
    expect(labels('gear', personas['owner at the PC'])).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('household member: no Admin, no Diagnostics', () => {
    expect(labels('header', personas['household member'])).toEqual(HEADER)
    expect(labels('gear', personas['household member'])).toEqual(['Jobs', 'Settings'])
  })

  it('member holding admin.diagnostics: Diagnostics without Admin', () => {
    expect(labels('gear', personas['member with admin.diagnostics'])).toEqual(['Jobs', 'Settings', 'Diagnostics'])
  })

  it('remote admin: Admin and Diagnostics, no Assistant', () => {
    expect(labels('header', personas['remote admin'])).toEqual(HEADER)
    expect(labels('gear', personas['remote admin'])).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('auth unavailable: renders as before sign-in existed, Admin included', () => {
    const ctx: NavContext = { session: { status: 'unavailable' }, pcMode: 'local', developerMode: false }
    expect(labels('gear', ctx)).toEqual(['Jobs', 'Settings', 'Admin', 'Diagnostics'])
  })

  it('session still loading: no Admin or Diagnostics', () => {
    const ctx: NavContext = { session: { status: 'loading' }, pcMode: 'unknown', developerMode: false }
    expect(labels('gear', ctx)).toEqual(['Jobs', 'Settings'])
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

  it('hides Diagnostics and Benchmark Lab by admin.diagnostics', () => {
    const by = Object.fromEntries(NAV_ITEMS.map((i) => [i.label, i]))
    expect(by['Diagnostics'].requires).toBe('admin.diagnostics')
    expect(by['Benchmark Lab'].requires).toBe('admin.diagnostics')
    expect(by['Diagnostics'].hideWithoutPermission).toBe(true)
    expect(by['Benchmark Lab'].hideWithoutPermission).toBe(true)
    expect(by['Benchmark Lab'].surface).toBe('none')
  })
})

const railLabels = (ctx: NavContext) => visibleRailGroups(ctx).flatMap((g) => g.items.map((r) => r.item.label))
const railCurrent = (ctx: NavContext, route: Route) =>
  visibleRailGroups(ctx).flatMap((g) => g.items).filter((r) => r.active.includes(route.name)).map((r) => r.item.label)

describe('left rail', () => {
  const owner = personas['owner at the PC']

  it('owner sees every page, grouped, with Benchmark Lab under Diagnostics', () => {
    expect(visibleRailGroups(owner).map((g) => [g.heading, g.items.map((r) => r.item.label)])).toEqual([
      [null, ['Library', 'Saved manga', 'Library tools']],
      ['Find and add', ['Sources', 'Discover']],
      ['Tools', ['Translate text', 'Live']],
      ['System', ['Jobs', 'Settings', 'Admin', 'Diagnostics', 'Benchmark Lab']],
    ])
  })

  it('household member: no Admin, Diagnostics or Benchmark Lab', () => {
    const labelsOf = railLabels(personas['household member'])
    expect(labelsOf).toEqual(['Library', 'Saved manga', 'Library tools', 'Sources', 'Discover', 'Translate text', 'Live', 'Jobs', 'Settings'])
  })

  it('member holding admin.diagnostics sees Diagnostics and Benchmark Lab but not Admin', () => {
    const labelsOf = railLabels(personas['member with admin.diagnostics'])
    expect(labelsOf).toEqual(expect.arrayContaining(['Diagnostics', 'Benchmark Lab']))
    expect(labelsOf).not.toContain('Admin')
  })

  it('the PC without sign-in sees every page', () => {
    const ctx: NavContext = { session: { status: 'unavailable' }, pcMode: 'local', developerMode: false }
    expect(railLabels(ctx)).toEqual(expect.arrayContaining(['Admin', 'Diagnostics', 'Benchmark Lab', 'Jobs']))
  })

  it('Assistant appears only on the PC in Developer Mode', () => {
    expect(railLabels({ ...owner, developerMode: true })).toContain('Assistant')
    expect(railLabels({ ...owner, pcMode: 'remote', developerMode: true })).not.toContain('Assistant')
    expect(railLabels({ ...owner, pcMode: 'unknown', developerMode: true })).not.toContain('Assistant')
  })

  it('lists every registered page with a rail slot, so none is reachable only by deep link', () => {
    const ctx: NavContext = { ...owner, developerMode: true }
    const targets = visibleRailGroups(ctx).flatMap((g) => g.items.map((r) => r.item.target.name))
    expect(targets).toEqual(
      expect.arrayContaining(['library', 'library-tools', 'manga', 'sources', 'discover', 'translate', 'live', 'settings', 'admin', 'diagnostics', 'benchmark', 'assistant']),
    )
  })

  it.each<[string, Route, string[]]>([
    ['library', { name: 'library' }, ['Library']],
    ['title workspace', { name: 'drama', id: 3, stage: 'review' }, ['Library']],
    ['reader', { name: 'read', id: 3, page: null }, ['Library']],
    ['comic', { name: 'comic', id: 3, page: 2 }, ['Library']],
    ['saved manga', { name: 'manga' }, ['Saved manga']],
    ['manga series', { name: 'manga-series', source: 's', series: 'x' }, ['Saved manga']],
    ['manga chapter', { name: 'manga-read', source: 's', series: 'x', chapter: '1', page: null }, ['Saved manga']],
    ['library tools', { name: 'library-tools' }, ['Library tools']],
    ['diagnostics', { name: 'diagnostics' }, ['Diagnostics']],
    ['benchmark', { name: 'benchmark' }, ['Benchmark Lab']],
    ['translate', { name: 'translate' }, ['Translate text']],
  ])('%s marks exactly one rail item current', (_name, route, expected) => {
    expect(railCurrent({ ...owner, developerMode: true }, route)).toEqual(expected)
  })
})

describe('rail collapse', () => {
  const store = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }

  it('starts expanded and remembers a collapse', () => {
    const s = store()
    expect(readPref(s, RAIL_COLLAPSED_KEY, false)).toBe(false)
    expect(writePref(s, RAIL_COLLAPSED_KEY, true)).toBe(true)
    expect(readPref(s, RAIL_COLLAPSED_KEY, false)).toBe(true)
    expect(s.getItem(PREF_KEY_PREFIX + RAIL_COLLAPSED_KEY)).toBe('true')
  })

  it('stays expanded when storage is unavailable or holds something else', () => {
    expect(readPref(null, RAIL_COLLAPSED_KEY, false)).toBe(false)
    const s = store()
    s.setItem(PREF_KEY_PREFIX + RAIL_COLLAPSED_KEY, '"yes"')
    expect(readPref(s, RAIL_COLLAPSED_KEY, false)).toBe(false)
  })
})
