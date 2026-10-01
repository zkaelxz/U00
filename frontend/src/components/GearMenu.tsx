/*
 * Header cogwheel: Settings and the pages behind it (Admin for admins,
 * Diagnostics, the Assistant in Developer Mode). They are used rarely, so
 * they sit in one menu instead of the main navigation.
 */
import { useDetailsMenu } from '../hooks/useDetailsMenu'
import { routeHref, type Route } from '../router'
import './gearMenu.css'

export type GearItem = { label: string; target: Route; active: Route['name'][] }

export function GearMenu({ items, route }: { items: GearItem[]; route: Route }) {
  const ref = useDetailsMenu()
  const here = items.some((i) => i.active.includes(route.name))
  return (
    <details className="gear-menu" ref={ref}>
      <summary aria-label="Settings and tools" aria-current={here ? 'page' : undefined}>
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
        </svg>
      </summary>
      <nav className="gear-menu-panel" aria-label="Settings and tools">
        {items.map((i) => (
          <a
            key={i.label}
            href={routeHref(i.target)}
            aria-current={i.active.includes(route.name) ? 'page' : undefined}
            onClick={() => {
              if (ref.current) ref.current.open = false
            }}
          >
            {i.label}
          </a>
        ))}
      </nav>
    </details>
  )
}
