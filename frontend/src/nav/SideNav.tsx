/*
 * Floating dock (or, when docked, a full-height rail) for screens 600px and wider: the shared nav groups as icons that
 * open into labelled rows when pointed at or focused (nav/sideNav.css).
 */
import { useEffect, useRef, useState } from 'react'
import { routeHref, type Route } from '../router'
import type { NavContext } from './navItems'
import { NavGroups } from './navLinks'
import { setNavDock, useNavDock } from './navDock'
import { createRailOpen } from './railOpen'
import './sideNav.css'
import './navPosition.css'

export function SideNav({ route, context }: { route: Route; context: NavContext }) {
  const mode = useNavDock()
  const docked = mode !== 'float'
  const minimized = mode === 'minimized'
  const [open, setOpen] = useState(false)
  const asideRef = useRef<HTMLElement>(null)
  const [rail] = useState(() => createRailOpen(setOpen))
  useEffect(() => {
    const onPress = (e: PointerEvent) => {
      if (!asideRef.current?.contains(e.target as Node)) rail.pressOutside()
    }
    document.addEventListener('pointerdown', onPress)
    return () => {
      document.removeEventListener('pointerdown', onPress)
      rail.dispose()
    }
  }, [rail])
  const classes = ['app-rail', docked && 'is-docked', minimized && 'is-minimized', open && 'is-open'].filter(Boolean).join(' ')
  return (
    <aside
      ref={asideRef}
      className={classes}
      onPointerEnter={rail.pointerEnter}
      onPointerLeave={rail.pointerLeave}
      onFocus={(e) => rail.focusIn(e.target.matches(':focus-visible'))}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) rail.focusOut()
      }}
    >
      <p className="rail-brand">
        <a href={routeHref({ name: 'library' })} title="Baihe Studio: Library">
          <span aria-hidden="true" className="rail-brand-mark">B</span>
          <span className="rail-label">Baihe Studio</span>
        </a>
      </p>
      <NavGroups route={route} context={context} collapsed />
      {docked && (
        <button
          type="button"
          className="rail-toggle rail-collapse"
          aria-pressed={minimized}
          title={minimized ? 'Expand the navigation' : 'Minimize the navigation'}
          onClick={() => setNavDock(minimized ? 'docked' : 'minimized')}
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            {minimized ? <path d="M9 6l6 6-6 6" /> : <path d="M15 6l-6 6 6 6" />}
          </svg>
          <span className="rail-label">{minimized ? 'Expand navigation' : 'Minimize navigation'}</span>
        </button>
      )}
      <button
        type="button"
        className="rail-toggle rail-dock"
        aria-pressed={docked}
        title={docked ? 'Float the navigation' : 'Dock the navigation'}
        onClick={() => setNavDock(docked ? 'float' : 'docked')}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M9 4v16" />
        </svg>
        <span className="rail-label">{docked ? 'Float navigation' : 'Dock navigation'}</span>
      </button>
    </aside>
  )
}
