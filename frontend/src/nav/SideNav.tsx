/*
 * Left rail for screens 1024px and wider: the shared nav groups, and a collapse
 * toggle that leaves an icon rail.
 */
import { routeHref, type Route } from '../router'
import type { NavContext } from './navItems'
import { Icon, NavGroups } from './navLinks'
import './sideNav.css'

export function SideNav({
  route,
  context,
  collapsed,
  onToggle,
}: {
  route: Route
  context: NavContext
  collapsed: boolean
  onToggle: () => void
}) {
  return (
    <aside className="app-rail" data-collapsed={collapsed}>
      <h1 className="rail-brand">
        <a href={routeHref({ name: 'library' })} title="Baihe Studio: Library">
          <span aria-hidden="true" className="rail-brand-mark">B</span>
          <span className="rail-label">Baihe Studio</span>
        </a>
      </h1>
      <NavGroups route={route} context={context} collapsed={collapsed} />
      <button
        type="button"
        className="rail-toggle"
        aria-label="Side menu"
        aria-expanded={!collapsed}
        title={collapsed ? 'Expand menu' : 'Collapse menu'}
        onClick={onToggle}
      >
        <Icon d={collapsed ? 'M9 6l6 6-6 6' : 'M15 6l-6 6 6 6'} />
        <span className="rail-label">Collapse</span>
      </button>
    </aside>
  )
}
