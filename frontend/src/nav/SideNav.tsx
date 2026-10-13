/*
 * Floating dock for screens 600px and wider: the shared nav groups as icons that
 * open into labelled rows when pointed at or focused (nav/sideNav.css).
 */
import { routeHref, type Route } from '../router'
import type { NavContext } from './navItems'
import { NavGroups } from './navLinks'
import './sideNav.css'

export function SideNav({ route, context }: { route: Route; context: NavContext }) {
  return (
    <aside className="app-rail">
      <p className="rail-brand">
        <a href={routeHref({ name: 'library' })} title="Baihe Studio: Library">
          <span aria-hidden="true" className="rail-brand-mark">B</span>
          <span className="rail-label">Baihe Studio</span>
        </a>
      </p>
      <NavGroups route={route} context={context} collapsed />
    </aside>
  )
}
