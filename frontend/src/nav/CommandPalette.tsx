/*
 * Ctrl+K quick search: a button for the header plus the dialog it opens. A
 * modal <dialog> keeps the page behind inert and Esc closes it; focus is put
 * back on whatever had it (the button, or a field the shortcut was pressed in)
 * because that element may not be the one the browser remembers.
 */
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { trapTabWithin, useModalDialog } from '../hooks/useModalDialog'
import type { Route } from '../router'
import type { NavContext } from './navItems'
import { filterEntries, isPaletteShortcut, paletteEntries } from './palette'
import './palette.css'

const FOCUSABLE = 'input, button:not([disabled])'
const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const SHORTCUT_LABEL = IS_MAC ? '⌘K' : 'Ctrl+K'

const trapTab = trapTabWithin(FOCUSABLE)

export function CommandPalette({ route, context }: { route: Route; context: NavContext }) {
  const [open, setOpen] = useState(false)
  const opener = useRef<HTMLElement | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const dialog = useModalDialog(open)

  const show = () => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : button.current
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    // After the dialog closes: the page is inert while a modal is open.
    requestAnimationFrame(() => (opener.current?.isConnected ? opener.current : button.current)?.focus())
  }

  // Held in a ref so the document listener is attached once.
  const toggle = useRef(() => {})
  useEffect(() => {
    toggle.current = () => (open ? close() : show())
  })
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!isPaletteShortcut(e)) return
      // Also stops Firefox's own Ctrl+K (focus the search bar).
      e.preventDefault()
      toggle.current()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // The drawer and other links change the hash from underneath; a stale dialog must not stay over the new page.
  const routeKey = JSON.stringify(route)
  useEffect(() => setOpen(false), [routeKey])

  return (
    <>
      <button
        ref={button}
        type="button"
        className="palette-btn"
        aria-haspopup="dialog"
        aria-label="Quick search"
        title={`Search pages (${SHORTCUT_LABEL})`}
        onClick={show}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5" />
        </svg>
        <kbd aria-hidden="true">{SHORTCUT_LABEL}</kbd>
      </button>
      <dialog
        ref={dialog}
        className="palette"
        aria-label="Quick search"
        onCancel={(e) => {
          e.preventDefault()
          close()
        }}
        onClose={() => open && close()}
        onKeyDown={trapTab}
        onClick={(e) => {
          if (e.target === e.currentTarget) close()
        }}
      >
        {open && <PaletteBody route={route} context={context} onClose={close} />}
      </dialog>
    </>
  )
}

function PaletteBody({ route, context, onClose }: { route: Route; context: NavContext; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listId = useId()
  const optionId = (i: number) => `${listId}-${i}`

  const entries = useMemo(() => paletteEntries(context, route), [context, route])
  const results = useMemo(() => filterEntries(entries, query), [entries, query])
  const at = Math.min(active, Math.max(results.length - 1, 0))

  function go(i: number, newTab: boolean) {
    const entry = results[i]
    if (!entry) return
    if (newTab) {
      window.open(entry.href, '_blank', 'noopener')
      return
    }
    // The same hash fires no hashchange, so the dialog closes here rather than relying on the route effect.
    window.location.hash = entry.href
    onClose()
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.nativeEvent.isComposing) return
    const last = results.length - 1
    const move = (to: number) => {
      e.preventDefault()
      setActive(to)
      document.getElementById(optionId(to))?.scrollIntoView?.({ block: 'nearest' })
    }
    if (e.key === 'ArrowDown') move(at >= last ? 0 : at + 1)
    else if (e.key === 'ArrowUp') move(at <= 0 ? Math.max(last, 0) : at - 1)
    else if (e.key === 'Home') move(0)
    else if (e.key === 'End') move(Math.max(last, 0))
    else if (e.key === 'Enter') {
      e.preventDefault()
      go(at, e.ctrlKey || e.metaKey)
    }
  }

  return (
    <div className="palette-body">
      <div className="palette-head">
        <input
          type="search"
          role="combobox"
          className="palette-input"
          aria-label="Search pages"
          aria-expanded={results.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={results.length > 0 ? optionId(at) : undefined}
          placeholder="Search pages"
          enterKeyHint="go"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="palette-close" aria-label="Close search" onClick={onClose}>
          ×
        </button>
      </div>
      <div id={listId} role="listbox" aria-label="Results" className="palette-list">
        {results.map((r, i) => (
          <a
            key={r.id}
            id={optionId(i)}
            role="option"
            aria-selected={i === at}
            tabIndex={-1}
            href={r.href}
            className="palette-option"
            onMouseMove={() => i !== at && setActive(i)}
            onClick={(e) => {
              // Ctrl/Cmd-click keeps the browser's new-tab behaviour.
              if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return
              onClose()
            }}
          >
            <span className="palette-label">{r.label}</span>
            <span className="palette-hint">{r.hint}</span>
          </a>
        ))}
      </div>
      <p role="status" className={results.length === 0 ? 'palette-empty' : 'visually-hidden'}>
        {results.length === 0 ? `No matches for “${query.trim()}”` : `${results.length} ${results.length === 1 ? 'result' : 'results'}`}
      </p>
    </div>
  )
}
