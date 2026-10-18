/*
 * Left drawer for screens narrower than 600px, opened by the Menu button or the phone bar's More. The drawer is a
 * modal <dialog>, so the page behind is inert, Esc closes it and the browser
 * returns focus to whatever opened it; the same NavGroups as the dock fill it.
 */
import { useEffect } from 'react'

import { ThemeMenu } from '../components/ThemeMenu'
import { openReportDialog } from '../report/reportDialogStore'
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

/** The header's hamburger, for pages where the phone bar is not shown. */
export function MenuButton({ open, onOpen }: { open: boolean; onOpen: () => void }) {
  return (
    <button type="button" className="menu-btn" aria-label="Menu" aria-expanded={open} aria-controls={DRAWER_ID} onClick={onOpen}>
      <Icon d="M4 6h16M4 12h16M4 18h16" />
    </button>
  )
}

export const NAV_DRAWER_ID = DRAWER_ID

export function NavDrawer({ route, context, open, onOpenChange }: { route: Route; context: NavContext; open: boolean; onOpenChange: (open: boolean) => void }) {
  const setOpen = onOpenChange
  const dialog = useModalDialog(open)

  // A modal dialog does not stop the page behind it from scrolling.
  useEffect(() => {
    if (!open) return
    document.documentElement.classList.add(LOCK_CLASS)
    return () => document.documentElement.classList.remove(LOCK_CLASS)
  }, [open])

  // Back/forward and links that change the hash from elsewhere also leave the drawer.
  const routeKey = JSON.stringify(route)
  useEffect(() => setOpen(false), [routeKey, setOpen])

  return (
    <>
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
            <div className="nav-drawer-foot">
              <button type="button" onClick={() => { setOpen(false); openReportDialog() }}>Report a problem</button>
              <div className="nav-drawer-theme">
                <span>Theme</span>
                <ThemeMenu />
              </div>
            </div>
          </div>
        )}
      </dialog>
    </>
  )
}
