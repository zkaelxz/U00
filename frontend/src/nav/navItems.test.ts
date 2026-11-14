import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../api/auth'
import type { SessionState } from '../hooks/useSession'
import type { Route } from '../router'
import { NAV_ITEMS, customizableNavItems, isNavLocked, visibleNavItemsFor, visibleRailGroups, type NavContext } from './navItems'

const owner = { id: 1, email: 'o@example.com', display_name: 'Owner', is_admin: true, is_local_owner: true }
const meOf = (over: Partial<AuthMe>): SessionState => ({
  status: 'ready',
  me: { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user: owner, permissions: [], ...over },
})

const MEMBER_PERMISSIONS = ['library.read', 'lines.read', 'lines.edit', 'review.use', 'jobs.start', 'jobs.cancel']

const personas: Record<string, NavContext> = {
  'owner at the PC': {
    session: meOf({ auth_enabled: false, zone: 'local', permissions: [...MEMBER_PERMISSIONS, 'admin.users.read', 'admin.users', 'admin.diagnostics', 'admin.settings', 'admin.library', 'library.create'] }),
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

const ALL = ['Library', 'Library tools', 'Text', 'Find', 'Live', 'Jobs', 'Settings']

// Add creates a title, so it follows the permission the Library's Add box needs.
const withMake = (l: string[]) => [...l.slice(0, 2), 'Add', ...l.slice(2)]

describe('nav registry per persona', () => {
  it('owner at the PC', () => {
    expect(labels(personas['owner at the PC'])).toEqual(withMake([...ALL, 'People and access', 'Diagnostics']))
  })

  it('household member: no People and access, no Diagnostics', () => {
    expect(labels(personas['household member'])).toEqual(ALL)
  })

  it('member holding admin.diagnostics: Diagnostics without People and access', () => {
    expect(labels(personas['member with admin.diagnostics'])).toEqual([...ALL, 'Diagnostics'])
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

  it('Simple leaves the admin and system pages out of the menu, Advanced lists them', () => {
    const owner = personas['owner at the PC']
    expect(labels({ ...owner, simple: true })).toEqual(['Library', 'Add', 'Text', 'Find', 'Live', 'Jobs', 'Settings'])
    expect(labels({ ...owner, simple: false })).toEqual(labels(owner))
    const dev: NavContext = { ...owner, developerMode: true }
    expect(labels({ ...dev, simple: true })).not.toEqual(expect.arrayContaining(['Benchmark Lab']))
    expect(labels({ ...dev, simple: true })).not.toContain('Assistant')
    expect(labels({ ...dev, simple: false })).toEqual(expect.arrayContaining(['Benchmark Lab', 'Assistant']))
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

describe('Benchmark Lab', () => {
  const base = personas['owner at the PC']

  it('needs Developer Mode as well as admin.diagnostics', () => {
    expect(labels(base)).not.toContain('Benchmark Lab')
    expect(labels({ ...base, developerMode: true })).toContain('Benchmark Lab')
    expect(labels({ ...personas['household member'], developerMode: true })).not.toContain('Benchmark Lab')
  })
})

describe('registry data', () => {
  it('has unique labels (the menus key on them)', () => {
    const l = NAV_ITEMS.map((i) => i.label)
    expect(new Set(l).size).toBe(l.length)
  })

  it('hides Diagnostics and Benchmark Lab by admin.diagnostics, and Benchmark Lab also without Developer Mode', () => {
    const by = Object.fromEntries(NAV_ITEMS.map((i) => [i.label, i]))
    expect(by['Diagnostics'].requires).toBe('admin.diagnostics')
    expect(by['Benchmark Lab'].requires).toBe('admin.diagnostics')
    expect(by['Diagnostics'].hideWithoutPermission).toBe(true)
    expect(by['Benchmark Lab'].hideWithoutPermission).toBe(true)
    expect(by['Benchmark Lab'].developerMode).toBe(true)
  })
})

const railLabels = (ctx: NavContext) => visibleRailGroups(ctx).flatMap((g) => g.items.map((r) => r.item.label))
const railCurrent = (ctx: NavContext, route: Route) =>
  visibleRailGroups(ctx).flatMap((g) => g.items).filter((r) => r.active.includes(route.name)).map((r) => r.item.label)

describe('left rail', () => {
  const owner = personas['owner at the PC']

  it('owner sees every page, grouped, with Benchmark Lab under Diagnostics once Developer Mode is on', () => {
    expect(visibleRailGroups({ ...owner, developerMode: true }).map((g) => [g.heading, g.items.map((r) => r.item.label)])).toEqual([
      [null, ['Library', 'Library tools']],
      ['Find and add', ['Add', 'Find']],
      ['Tools', ['Text', 'Live']],
      ['System', ['Jobs', 'Settings', 'People and access', 'Diagnostics', 'Benchmark Lab', 'Assistant']],
    ])
  })

  it('leaves Benchmark Lab out of the rail with Developer Mode off', () => {
    expect(railLabels(owner)).not.toContain('Benchmark Lab')
  })

  it('household member: no People and access, Diagnostics or Benchmark Lab', () => {
    const labelsOf = railLabels(personas['household member'])
    expect(labelsOf).toEqual(['Library', 'Library tools', 'Find', 'Text', 'Live', 'Jobs', 'Settings'])
  })

  it('member holding admin.diagnostics sees Diagnostics and Benchmark Lab (Developer Mode on) but not People and access', () => {
    const labelsOf = railLabels({ ...personas['member with admin.diagnostics'], developerMode: true })
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
      expect.arrayContaining(['library', 'library-tools', 'discover', 'translate', 'make-subtitles', 'live', 'settings', 'admin', 'diagnostics', 'benchmark', 'assistant']),
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
    ['translate', { name: 'translate' }, ['Text']],
    ['make subtitles', { name: 'make-subtitles' }, ['Add']],
  ])('%s marks exactly one rail item current', (_name, route, expected) => {
    expect(railCurrent({ ...owner, developerMode: true }, route)).toEqual(expected)
  })
})

describe('Add menu item', () => {
  const labels = (ctx: NavContext) => visibleNavItemsFor(ctx).map((i) => i.label)

  it('sits first in the Find and add group and can be hidden like the others', () => {
    const owner = personas['owner at the PC']
    const find = visibleRailGroups(owner).find((g) => g.id === 'find')
    expect(find?.items.map((r) => r.item.label)).toEqual(['Add', 'Find'])
    expect(labels({ ...owner, hidden: ['make-subtitles'] })).not.toContain('Add')
    expect(customizableNavItems(owner).map((i) => i.label)).toContain('Add')
    expect(isNavLocked(NAV_ITEMS.find((i) => i.label === 'Add')!)).toBe(false)
  })

  it('is not offered to people who cannot create titles', () => {
    expect(labels(personas['household member'])).not.toContain('Add')
  })
})

describe('feature switches', () => {
  const owner = personas['owner at the PC']
  const labels = (ctx: NavContext) => visibleNavItemsFor(ctx).map((i) => i.label)

  it('shows Find when no switch is reported or Sources is on', () => {
    expect(labels(owner)).toContain('Find')
    expect(labels({ ...owner, features: { jellyfin: false } })).toContain('Find')
    expect(labels({ ...owner, features: { sources: true } })).toContain('Find')
  })

  it('keeps Find when Sources is switched off, since Discover still works', () => {
    const off = { ...owner, features: { sources: false } }
    expect(labels(off)).toContain('Find')
    expect(visibleRailGroups(off).flatMap((g) => g.items.map((r) => r.item.label))).toContain('Find')
  })

  it('lights Find for the Discover page and both Sources routes', () => {
    const find = visibleRailGroups(owner).flatMap((g) => g.items).find((r) => r.item.label === 'Find')
    expect(find?.active).toEqual(['discover', 'sources', 'sources-manage'])
  })
})
