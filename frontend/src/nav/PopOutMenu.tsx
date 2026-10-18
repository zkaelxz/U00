import { useDetailsMenu } from '../hooks/useDetailsMenu'
import { buttonClass } from '../components/uiClasses'
import { routeHref, type Route } from '../router'
import { visibleNavItemsFor, type NavContext } from './navItems'
import './popOutMenu.css'

/** The page switcher of a pop-out window, which has no side nav: every page the side nav lists. */
export function PopOutMenu({ route, context }: { route: Route; context: NavContext }) {
  const ref = useDetailsMenu()
  const items = visibleNavItemsFor(context)
  const current = items.find((i) => i.target.name === route.name)
  return (
    <details className="popout-menu" ref={ref}>
      <summary className={buttonClass('secondary', 'sm')}>{current?.label ?? 'Pages'} ▾</summary>
      <nav className="popout-menu-panel" aria-label="Pages">
        {items.map((i) => (
          <a key={i.label} href={routeHref(i.target)} aria-current={i.target.name === route.name ? 'page' : undefined} onClick={() => { if (ref.current) ref.current.open = false }}>
            {i.label}
          </a>
        ))}
      </nav>
    </details>
  )
}
