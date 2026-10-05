/*
 * One list of the app's navigation items. The header's main nav, the cogwheel
 * menu and the wide-screen left rail render from it, so a new page is added
 * here once.
 *
 * `requires` records the permission the page's data routes need
 * (docs/remote-access-decision.md). It hides an item only where
 * `hideWithoutPermission` is set; the server stays the authority either way.
 */
import type { PcMode } from '../api/pcOnly'
import { holds } from '../pages/diagnostics/adminUsers'
import type { Route } from '../router'
import type { SessionState } from '../hooks/useSession'

export type NavSurface = 'header' | 'gear' | 'none'

/** usePersistedState key for the rail's collapsed state. */
export const RAIL_COLLAPSED_KEY = 'nav.collapsed'

export type RailGroup = 'library' | 'find' | 'tools' | 'system'

/** Rail groups in on-screen order; the first has no heading because the Library item itself heads it. */
export const RAIL_GROUPS: { id: RailGroup; heading: string | null }[] = [
  { id: 'library', heading: null },
  { id: 'find', heading: 'Find and add' },
  { id: 'tools', heading: 'Tools' },
  { id: 'system', heading: 'System' },
]

/** Where an item sits in the left rail. `active` is separate from the item's own because the rail has a row per page where the header folds several into one. */
export interface RailSlot {
  group: RailGroup
  active: Route['name'][]
}

export interface NavItem {
  label: string
  target: Route
  /** Route names that mark this item as the current page. */
  active: Route['name'][]
  surface: NavSurface
  /** Permission the page's reads need; null when any signed-in caller may open it. */
  requires: string | null
  /** Hide the item from callers without `requires`. Off where the page still shows something useful or the owner hasn't decided. */
  hideWithoutPermission: boolean
  /** Shown only on the main PC. */
  pcOnly: boolean
  /** Shown only while Developer Mode is on. */
  developerMode: boolean
  /** A live count shown beside the label; the surface that renders the item supplies the number. */
  badge: 'running-jobs' | null
  /** Null when the left rail does not list the item. */
  rail: RailSlot | null
}

function item(
  label: string,
  target: Route,
  active: Route['name'][],
  surface: NavSurface,
  over: Partial<Pick<NavItem, 'requires' | 'hideWithoutPermission' | 'pcOnly' | 'developerMode' | 'rail' | 'badge'>> = {},
): NavItem {
  return { label, target, active, surface, requires: null, hideWithoutPermission: false, pcOnly: false, developerMode: false, rail: null, badge: null, ...over }
}

// Order is the on-screen order within each surface, and within each rail group.
export const NAV_ITEMS: NavItem[] = [
  item('Library', { name: 'library' }, ['library', 'library-tools', 'drama', 'read', 'comic', 'manga', 'manga-series', 'manga-read'], 'header', {
    requires: 'library.read',
    rail: { group: 'library', active: ['library', 'drama', 'read', 'comic'] },
  }),
  item('Saved manga', { name: 'manga' }, ['manga', 'manga-series', 'manga-read'], 'none', {
    requires: 'library.read',
    rail: { group: 'library', active: ['manga', 'manga-series', 'manga-read'] },
  }),
  item('Library tools', { name: 'library-tools' }, ['library-tools'], 'none', {
    requires: 'library.read',
    rail: { group: 'library', active: ['library-tools'] },
  }),
  item('Translate text', { name: 'translate' }, ['translate'], 'header', {
    requires: 'library.read',
    rail: { group: 'tools', active: ['translate'] },
  }),
  item('Sources', { name: 'sources' }, ['sources'], 'header', { requires: 'library.read', rail: { group: 'find', active: ['sources'] } }),
  item('Discover', { name: 'discover' }, ['discover'], 'header', { requires: 'library.read', rail: { group: 'find', active: ['discover'] } }),
  item('Live', { name: 'live' }, ['live'], 'header', { requires: 'library.read', rail: { group: 'tools', active: ['live'] } }),
  // The header button is the glance; this is the page. Below 1024px it sits in the gear menu so the phone's 3 by 2 header grid keeps its shape.
  item('Jobs', { name: 'jobs' }, ['jobs'], 'gear', {
    requires: 'library.read',
    badge: 'running-jobs',
    rail: { group: 'system', active: ['jobs'] },
  }),
  // A member sees only Sharing and devices here, which needs library.read.
  item('Settings', { name: 'settings' }, ['settings'], 'gear', { requires: 'library.read', rail: { group: 'system', active: ['settings'] } }),
  item('Admin', { name: 'admin' }, ['admin'], 'gear', {
    requires: 'admin.users.read',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['admin'] },
  }),
  // The gear folds Benchmark Lab into Diagnostics' highlight; the rail has a row for each.
  item('Diagnostics', { name: 'diagnostics' }, ['diagnostics', 'benchmark'], 'gear', {
    requires: 'admin.diagnostics',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['diagnostics'] },
  }),
  item('Benchmark Lab', { name: 'benchmark' }, ['benchmark'], 'none', {
    requires: 'admin.diagnostics',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['benchmark'] },
  }),
  // The Assistant's routes are local_only(); the page also waits for Developer Mode.
  item('Assistant', { name: 'assistant' }, ['assistant'], 'gear', { pcOnly: true, developerMode: true, rail: { group: 'system', active: ['assistant'] } }),
]

export interface NavContext {
  session: SessionState
  pcMode: PcMode
  developerMode: boolean
}

function isVisible(i: NavItem, ctx: NavContext): boolean {
  if (i.hideWithoutPermission && i.requires && !holds(ctx.session, i.requires)) return false
  // 'unknown' hides PC-only items: they wait for /api/meta rather than showing optimistically.
  if (i.pcOnly && ctx.pcMode !== 'local') return false
  if (i.developerMode && !ctx.developerMode) return false
  return true
}

export function visibleNavItems(surface: NavSurface, ctx: NavContext): NavItem[] {
  return NAV_ITEMS.filter((i) => i.surface === surface && isVisible(i, ctx))
}

export interface RailItem {
  item: NavItem
  active: Route['name'][]
}

/** The left rail's groups for this person; a group with no visible item is dropped. */
export function visibleRailGroups(ctx: NavContext): { id: RailGroup; heading: string | null; items: RailItem[] }[] {
  return RAIL_GROUPS.map(({ id, heading }) => ({
    id,
    heading,
    items: NAV_ITEMS.filter((i) => i.rail?.group === id && isVisible(i, ctx)).map((i) => ({ item: i, active: i.rail!.active })),
  })).filter((g) => g.items.length > 0)
}
