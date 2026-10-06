/*
 * One list of the app's navigation items. The left rail, the nav drawer, the
 * command palette and the Customize menu all read it, so a new page is added
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

/** usePersistedState key for the rail's collapsed state. */
export const RAIL_COLLAPSED_KEY = 'nav.collapsed'

/** Viewport width from which the rail starts expanded when the viewer never chose. */
export const RAIL_EXPANDED_MIN_WIDTH = 1280

/** A saved choice wins at every width; with none, narrow viewports start collapsed so pages keep their width. */
export function resolveRailCollapsed(saved: boolean | null, wideEnoughToExpand: boolean): boolean {
  return saved ?? !wideEnoughToExpand
}

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
  over: Partial<Pick<NavItem, 'requires' | 'hideWithoutPermission' | 'pcOnly' | 'developerMode' | 'rail' | 'badge'>> = {},
): NavItem {
  return { label, target, requires: null, hideWithoutPermission: false, pcOnly: false, developerMode: false, rail: null, badge: null, ...over }
}

// Order is the on-screen order within each rail group.
export const NAV_ITEMS: NavItem[] = [
  item('Library', { name: 'library' }, {
    requires: 'library.read',
    rail: { group: 'library', active: ['library', 'drama', 'read', 'comic'] },
  }),
  item('Saved manga', { name: 'manga' }, {
    requires: 'library.read',
    rail: { group: 'library', active: ['manga', 'manga-series', 'manga-read'] },
  }),
  item('Library tools', { name: 'library-tools' }, {
    requires: 'library.read',
    rail: { group: 'library', active: ['library-tools'] },
  }),
  item('Translate text', { name: 'translate' }, {
    requires: 'library.read',
    rail: { group: 'tools', active: ['translate'] },
  }),
  item('Sources', { name: 'sources' }, { requires: 'library.read', rail: { group: 'find', active: ['sources'] } }),
  item('Discover', { name: 'discover' }, { requires: 'library.read', rail: { group: 'find', active: ['discover'] } }),
  item('Live', { name: 'live' }, { requires: 'library.read', rail: { group: 'tools', active: ['live'] } }),
  // The header's Jobs button is the glance; this is the page.
  item('Jobs', { name: 'jobs' }, {
    requires: 'library.read',
    badge: 'running-jobs',
    rail: { group: 'system', active: ['jobs'] },
  }),
  // A member sees only Sharing and devices here, which needs library.read.
  item('Settings', { name: 'settings' }, { requires: 'library.read', rail: { group: 'system', active: ['settings'] } }),
  item('Admin', { name: 'admin' }, {
    requires: 'admin.users.read',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['admin'] },
  }),
  item('Diagnostics', { name: 'diagnostics' }, {
    requires: 'admin.diagnostics',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['diagnostics'] },
  }),
  item('Benchmark Lab', { name: 'benchmark' }, {
    requires: 'admin.diagnostics',
    hideWithoutPermission: true,
    rail: { group: 'system', active: ['benchmark'] },
  }),
  // The Assistant's routes are local_only(); the page also waits for Developer Mode.
  item('Assistant', { name: 'assistant' }, { pcOnly: true, developerMode: true, rail: { group: 'system', active: ['assistant'] } }),
]

export interface NavContext {
  session: SessionState
  pcMode: PcMode
  developerMode: boolean
  /** Ids (`navId`) this person chose to hide; locked items are never hidden whatever is listed. */
  hidden?: readonly string[]
}

/** Stable id for hiding: the target route name, which no two items share. */
export const navId = (i: NavItem): string => i.target.name

/** Library and Settings stay so a person cannot hide the way back to their titles or to this choice. */
const LOCKED_NAV_IDS: readonly string[] = ['library', 'settings']
export const isNavLocked = (i: NavItem): boolean => LOCKED_NAV_IDS.includes(navId(i))

function isHiddenByChoice(i: NavItem, ctx: NavContext): boolean {
  return !isNavLocked(i) && (ctx.hidden ?? []).includes(navId(i))
}

function isAllowed(i: NavItem, ctx: NavContext): boolean {
  if (i.hideWithoutPermission && i.requires && !holds(ctx.session, i.requires)) return false
  // 'unknown' hides PC-only items: they wait for /api/meta rather than showing optimistically.
  if (i.pcOnly && ctx.pcMode !== 'local') return false
  if (i.developerMode && !ctx.developerMode) return false
  return true
}

function isVisible(i: NavItem, ctx: NavContext): boolean {
  return isAllowed(i, ctx) && !isHiddenByChoice(i, ctx)
}

/** Every item this person may see before their own hiding, once each in registry order: the "Customize menu" list. */
export function customizableNavItems(ctx: NavContext): NavItem[] {
  return NAV_ITEMS.filter((i) => isAllowed(i, ctx))
}

/** Items the menu shows this person, hiding applied; the palette reads this too. */
export function visibleNavItemsFor(ctx: NavContext): NavItem[] {
  return customizableNavItems(ctx).filter((i) => !isHiddenByChoice(i, ctx))
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
