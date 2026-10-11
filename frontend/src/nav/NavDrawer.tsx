/*
 * Menu button and left drawer for screens narrower than 1024px. The drawer is a
 * modal <dialog>, so the page behind is inert, Esc closes it and the browser
 * returns focus to the button; the same NavGroups as the wide rail fill it.
 */
import { useEffect, useRef, useState } from 'react'

import { trapTabWithin, useModalDialog } from '../hooks/useModalDialog'
import type { Route } from '../router'
import { routeHref } from '../router'
import type { NavContext } from './navItems'
import { Icon, NavGroups } from './navLinks'
import './navDrawer.css'

const DRAWER_ID = 'nav-drawer'
const LOCK_CLASS = 'nav-locked'
const FOCUSABLE = 'a[href], button:not([disabled])'

const trapTab = trapTabWithin(FOCUSABLE)

export function NavDrawer({ route, context }: { route: Route; context: NavContext }) {
  const [open, setOpen] = useState(false)
  const dialog = useModalDialog(open)
  const button = useRef<HTMLButtonElement>(null)

  // A modal dialog does not stop the page behind it from scrolling.
  useEffect(() => {
    if (!open) return
    document.documentElement.classList.add(LOCK_CLASS)
    return () => document.documentElement.classList.remove(LOCK_CLASS)
  }, [open])

  // Back/forward and links that change the hash from elsewhere also leave the drawer.
  const routeKey = JSON.stringify(route)
  useEffect(() => setOpen(false), [routeKey])

  return (
    <>
      <button
        ref={button}
        type="button"
        className="menu-btn"
        aria-label="Menu"
        aria-expanded={open}
        aria-controls={DRAWER_ID}
        onClick={() => setOpen(true)}
      >
        <Icon d="M4 6h16M4 12h16M4 18h16" />
      </button>
      <dialog
        ref={dialog}
        id={DRAWER_ID}
        className="nav-drawer"
        aria-label="Main menu"
        onCancel={(e) => {
          e.preventDefault()
          setOpen(false)
        }}
        onClose={() => setOpen(false)}
        onKeyDown={trapTab}
        onClick={(e) => {
          if (e.target === e.currentTarget) setOpen(false)
        }}
      >
        {open && (
          <div className="nav-drawer-body">
            <div className="nav-drawer-head">
              <a className="nav-drawer-brand" href={routeHref({ name: 'library' })} onClick={() => setOpen(false)}>
                Baihe Studio
              </a>
              <button type="button" className="nav-drawer-close" aria-label="Close menu" onClick={() => setOpen(false)}>
                ×
              </button>
            </div>
            <NavGroups route={route} context={context} onNavigate={() => setOpen(false)} />
          </div>
        )}
      </dialog>
    </>
  )
}
