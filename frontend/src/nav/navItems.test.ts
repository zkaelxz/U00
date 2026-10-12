import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../api/auth'
import type { SessionState } from '../hooks/useSession'
import type { Route } from '../router'
import { readSavedRailChoice } from './useRailCollapsed'
import { PREF_KEY_PREFIX, readPref, writePref } from '../hooks/usePersistedState'
import { NAV_ITEMS, RAIL_COLLAPSED_KEY, customizableNavItems, isNavLocked, resolveRailCollapsed, visibleNavItemsFor, visibleRailGroups, type NavContext } from './navItems'

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
  // Only the PC listener can report admin.diagnostics; the household listener strips admin.* except admin.users.read.
  'member with admin.diagnostics': {
    session: meOf({ user: { ...owner, is_admin: false, is_local_owner: false }, permissions: [...MEMBER_PERMISSIONS, 'admin.diagnostics'] }),
    pcMode: 'local',
    developerMode: false,
  },
  'remote admin': {
    session: meOf({ user: { ...owner, is_local_owner: false }, permissions: [...MEMBER_PERMISSIONS, 'admin.users.read'] }),
    pcMode: 'remote',
    developerMode: false,
  },
}

const labels = (ctx: NavContext) => visibleNavItemsFor(ctx).map((i) => i.label)

const ALL = ['Library', 'Library tools', 'Quick translate', 'Sources', 'Discover', 'Live', 'Jobs', 'Settings']

// Make subtitles creates a title, so it follows the permission the Library's New title needs.
const withMake = (l: string[]) => [...l.slice(0, 2), 'Make subtitles', ...l.slice(2)]

describe('nav registry per persona', () => {
  it('owner at the PC', () => {
    expect(labels(personas['owner at the PC'])).toEqual(withMake([...ALL, 'People and access', 'Diagnostics', 'Benchmark Lab']))
  })

  it('household member: no People and access, no Diagnostics', () => {
    expect(labels(personas['household member'])).toEqual(ALL)
  })

  it('member holding admin.diagnostics: Diagnostics without People and access', () => {
    expect(labels(personas['member with admin.diagnostics'])).toEqual([...ALL, 'Diagnostics', 'Benchmark Lab'])
  })

  it('remote admin: People and access, no Diagnostics (the household listener never reports admin.diagnostics), no Assistant', () => {
    expect(labels(personas['remote admin'])).toEqual([...ALL, 'People and access'])
  })

  it('auth unavailable: every page but the admin ones until /me answers', () => {
    const ctx: NavContext = { session: { status: 'unavailable' }, pcMode: 'local', developerMode: false }
    expect(labels(ctx)).toEqual(ALL)
  })

  it('session still loading: no People and access or Diagnostics', () => {
    const ctx: NavContext = { session: { status: 'loading' }, pcMode: 'unknown', developerMode: false }
    expect(labels(ctx)).toEqual(ALL)
  })

  it('a hidden item drops out, but Library and Settings are locked in', () => {
    const owner = personas['owner at the PC']
    expect(labels({ ...owner, hidden: ['live', 'library', 'settings'] })).not.toContain('Live')
    expect(labels({ ...owner, hidden: ['library', 'settings'] })).toEqual(expect.arrayContaining(['Library', 'Settings']))
  })
})

describe('Assistant', () => {
  const base = personas['owner at the PC']

  it('needs Developer Mode on the PC', () => {
    expect(labels({ ...base, developerMode: true })).toContain('Assistant')
    expect(labels(base)).not.toContain('Assistant')
  })

  it('waits for /api/meta: hidden while the PC check is unknown or remote', () => {
    expect(labels({ ...base, pcMode: 'unknown', developerMode: true })).not.toContain('Assistant')
    expect(labels({ ...base, pcMode: 'remote', developerMode: true })).not.toContain('Assistant')
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
  })
})

const railLabels = (ctx: NavContext) => visibleRailGroups(ctx).flatMap((g) => g.items.map((r) => r.item.label))
const railCurrent = (ctx: NavContext, route: Route) =>
  visibleRailGroups(ctx).flatMap((g) => g.items).filter((r) => r.active.includes(route.name)).map((r) => r.item.label)

describe('left rail', () => {
  const owner = personas['owner at the PC']

  it('owner sees every page, grouped, with Benchmark Lab under Diagnostics', () => {
    expect(visibleRailGroups(owner).map((g) => [g.heading, g.items.map((r) => r.item.label)])).toEqual([
      [null, ['Library', 'Library tools']],
      ['Find and add', ['Sources', 'Discover']],
      ['Tools', ['Make subtitles', 'Quick translate', 'Live']],
      ['System', ['Jobs', 'Settings', 'People and access', 'Diagnostics', 'Benchmark Lab']],
    ])
  })

  it('household member: no People and access, Diagnostics or Benchmark Lab', () => {
    const labelsOf = railLabels(personas['household member'])
    expect(labelsOf).toEqual(['Library', 'Library tools', 'Sources', 'Discover', 'Quick translate', 'Live', 'Jobs', 'Settings'])
  })

  it('member holding admin.diagnostics sees Diagnostics and Benchmark Lab but not People and access', () => {
    const labelsOf = railLabels(personas['member with admin.diagnostics'])
    expect(labelsOf).toEqual(expect.arrayContaining(['Diagnostics', 'Benchmark Lab']))
    expect(labelsOf).not.toContain('People and access')
  })

  it('a failed /me still renders every non-admin page', () => {
    const ctx: NavContext = { session: { status: 'unavailable' }, pcMode: 'local', developerMode: false }
    const labelsOf = railLabels(ctx)
    expect(labelsOf).toContain('Jobs')
    expect(labelsOf).not.toEqual(expect.arrayContaining(['People and access']))
    expect(labelsOf).not.toContain('Diagnostics')
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
      expect.arrayContaining(['library', 'library-tools', 'sources', 'discover', 'translate', 'make-subtitles', 'live', 'settings', 'admin', 'diagnostics', 'benchmark', 'assistant']),
    )
  })

  it.each<[string, Route, string[]]>([
    ['library', { name: 'library' }, ['Library']],
    ['title workspace', { name: 'drama', id: 3, stage: 'review' }, ['Library']],
    ['reader', { name: 'read', id: 3, page: null }, ['Library']],
    ['comic', { name: 'comic', id: 3, page: 2 }, ['Library']],
    ['saved manga', { name: 'manga' }, ['Library tools']],
    ['manga series', { name: 'manga-series', source: 's', series: 'x' }, ['Library tools']],
    ['manga chapter', { name: 'manga-read', source: 's', series: 'x', chapter: '1', page: null }, ['Library tools']],
    ['library tools', { name: 'library-tools' }, ['Library tools']],
    ['diagnostics', { name: 'diagnostics' }, ['Diagnostics']],
    ['benchmark', { name: 'benchmark' }, ['Benchmark Lab']],
    ['translate', { name: 'translate' }, ['Quick translate']],
    ['make subtitles', { name: 'make-subtitles' }, ['Make subtitles']],
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

  it('tells a never-made choice from a saved one', () => {
    const s = store()
    expect(readSavedRailChoice(s)).toBeNull()
    writePref(s, RAIL_COLLAPSED_KEY, false)
    expect(readSavedRailChoice(s)).toBe(false)
    writePref(s, RAIL_COLLAPSED_KEY, true)
    expect(readSavedRailChoice(s)).toBe(true)
    expect(readSavedRailChoice(null)).toBeNull()
  })

  it.each<[string, boolean | null, boolean, boolean]>([
    ['no saved value, narrower than 1280', null, false, true],
    ['no saved value, 1280 or wider', null, true, false],
    ['saved expanded wins when narrow', false, false, false],
    ['saved collapsed wins when wide', true, true, true],
  ])('default: %s', (_name, saved, wideEnough, collapsed) => {
    expect(resolveRailCollapsed(saved, wideEnough)).toBe(collapsed)
  })
})

describe('Make subtitles menu item', () => {
  const labels = (ctx: NavContext) => visibleNavItemsFor(ctx).map((i) => i.label)

  it('sits in the Tools group and can be hidden like the others', () => {
    const owner = personas['owner at the PC']
    const tools = visibleRailGroups(owner).find((g) => g.id === 'tools')
    expect(tools?.items.map((r) => r.item.label)).toContain('Make subtitles')
    expect(labels({ ...owner, hidden: ['make-subtitles'] })).not.toContain('Make subtitles')
    expect(customizableNavItems(owner).map((i) => i.label)).toContain('Make subtitles')
    expect(isNavLocked(NAV_ITEMS.find((i) => i.label === 'Make subtitles')!)).toBe(false)
  })

  it('is not offered to people who cannot create titles', () => {
    expect(labels(personas['household member'])).not.toContain('Make subtitles')
  })
})
