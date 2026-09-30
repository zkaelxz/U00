/*
 * Header theme button: a menu of radio items (Match this device, Light, Dark,
 * Sepia): the only place the theme is changed. The choice is kept by
 * src/theme.ts, which the Reader's "Match app" theme also reads.
 *
 * Keyboard: Enter/Space/ArrowDown on the button opens it on the current
 * item; Up/Down/Home/End move; Enter/Space picks and closes; Escape closes
 * and returns to the button; Tab or a click elsewhere closes.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'

import { setThemePref, THEME_OPTIONS, themeLabel, useThemePref, type ThemePref } from '../theme'
import './themeMenu.css'

const svg = (children: ReactNode) => (
  <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    {children}
  </svg>
)

const ICONS: Record<ThemePref, ReactNode> = {
  system: svg(<><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>),
  light: svg(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  dark: svg(<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />),
  sepia: svg(<><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 21V5M9 7h6" /></>),
}

export function ThemeMenuItems({ id, theme, itemRefs, onKeyDown, onPick }: {
  id: string
  theme: ThemePref
  itemRefs: { current: (HTMLButtonElement | null)[] }
  onKeyDown: (e: KeyboardEvent) => void
  onPick: (pref: ThemePref) => void
}) {
  return (
    <div id={id} className="theme-menu-panel" role="menu" aria-label="Theme" onKeyDown={onKeyDown}>
      {THEME_OPTIONS.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => {
            itemRefs.current[i] = el
          }}
          type="button"
          role="menuitemradio"
          aria-checked={o.value === theme}
          tabIndex={o.value === theme ? 0 : -1}
          className="theme-menu-item"
          onClick={() => onPick(o.value)}
        >
          {ICONS[o.value]}
          <span>{o.label}</span>
          <span className="theme-menu-check" aria-hidden="true">{o.value === theme ? '✓' : ''}</span>
        </button>
      ))}
    </div>
  )
}

export function ThemeMenu() {
  const theme = useThemePref()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const items = useRef<(HTMLButtonElement | null)[]>([])
  const menuId = useId()
  const current = Math.max(0, THEME_OPTIONS.findIndex((o) => o.value === theme))

  // Focus the current item when the menu opens.
  useEffect(() => {
    if (open) items.current[current]?.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on open
  }, [open])

  useEffect(() => {
    if (!open) return
    const away = (e: Event) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])

  function close(refocus: boolean) {
    setOpen(false)
    if (refocus) button.current?.focus()
  }

  function onMenuKey(e: KeyboardEvent) {
    const at = items.current.findIndex((el) => el === document.activeElement)
    const n = THEME_OPTIONS.length
    const move = (i: number) => {
      e.preventDefault()
      items.current[(i + n) % n]?.focus()
    }
    if (e.key === 'ArrowDown') move(at + 1)
    else if (e.key === 'ArrowUp') move(at - 1)
    else if (e.key === 'Home') move(0)
    else if (e.key === 'End') move(n - 1)
    else if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
    } else if (e.key === 'Tab') close(false)
  }

  return (
    <div className="theme-menu" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="theme-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Theme: ${themeLabel(theme)}. Change theme`}
        title={`Theme: ${themeLabel(theme)}`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !open) {
            e.preventDefault()
            setOpen(true)
          } else if (e.key === 'Escape' && open) close(false)
        }}
      >
        {ICONS[theme]}
      </button>
      {open && (
        <ThemeMenuItems
          id={menuId}
          theme={theme}
          itemRefs={items}
          onKeyDown={onMenuKey}
          onPick={(pref) => {
            setThemePref(pref)
            close(true)
          }}
        />
      )}
    </div>
  )
}
