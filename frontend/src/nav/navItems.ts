/*
 * One list of the app's navigation items. The header's main nav and the
 * cogwheel menu render from it, so a new page is added here once.
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
}

function item(
  label: string,
  target: Route,
  active: Route['name'][],
  surface: NavSurface,
  over: Partial<Pick<NavItem, 'requires' | 'hideWithoutPermission' | 'pcOnly' | 'developerMode' | 'badge'>> = {},
): NavItem {
  return { label, target, active, surface, requires: null, hideWithoutPermission: false, pcOnly: false, developerMode: false, badge: null, ...over }
}

// Order is the on-screen order within each surface.
export const NAV_ITEMS: NavItem[] = [
  item('Library', { name: 'library' }, ['library', 'library-tools', 'drama', 'read', 'comic', 'manga', 'manga-series', 'manga-read'], 'header', { requires: 'library.read' }),
  item('Quick translate', { name: 'translate' }, ['translate'], 'header', { requires: 'library.read' }),
  item('Sources', { name: 'sources' }, ['sources'], 'header', { requires: 'library.read' }),
  item('Discover', { name: 'discover' }, ['discover'], 'header', { requires: 'library.read' }),
  item('Live', { name: 'live' }, ['live'], 'header', { requires: 'library.read' }),
  // The header button is the glance; this is the page. Until the side menu exists it sits in the gear menu so the phone's 3 by 2 header grid keeps its shape.
  item('Jobs', { name: 'jobs' }, ['jobs'], 'gear', { requires: 'library.read', badge: 'running-jobs' }),
  // A member sees only Sharing and devices here, which needs library.read.
  item('Settings', { name: 'settings' }, ['settings'], 'gear', { requires: 'library.read' }),
  item('Admin', { name: 'admin' }, ['admin'], 'gear', { requires: 'admin.users.read', hideWithoutPermission: true }),
  // Diagnostics is also the highlight for Benchmark Lab, which is reached from its page.
  item('Diagnostics', { name: 'diagnostics' }, ['diagnostics', 'benchmark'], 'gear', { requires: 'admin.diagnostics' }),
  item('Benchmark Lab', { name: 'benchmark' }, ['benchmark'], 'none', { requires: 'admin.diagnostics' }),
  // The Assistant's routes are local_only(); the page also waits for Developer Mode.
  item('Assistant', { name: 'assistant' }, ['assistant'], 'gear', { pcOnly: true, developerMode: true }),
]

export interface NavContext {
  session: SessionState
  pcMode: PcMode
  developerMode: boolean
}

export function visibleNavItems(surface: NavSurface, ctx: NavContext): NavItem[] {
  return NAV_ITEMS.filter((i) => {
    if (i.surface !== surface) return false
    if (i.hideWithoutPermission && i.requires && !holds(ctx.session, i.requires)) return false
    // 'unknown' hides PC-only items: they wait for /api/meta rather than showing optimistically.
    if (i.pcOnly && ctx.pcMode !== 'local') return false
    if (i.developerMode && !ctx.developerMode) return false
    return true
  })
}
