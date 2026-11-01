/*
 * "/" and "?" outside the Review editor: "/" focuses the page's search box, "?" opens the list of
 * keys. Review keeps its own "/" and "?" (it has a search box and a longer list), so a title
 * workspace is left alone here rather than having two listeners answer one key.
 */
import { useEffect, useState } from 'react'

import { useShortcut } from '../hooks/useShortcut'
import { Sheet } from '../components/Sheet'
import { REVIEW_EDITING_KEYS, REVIEW_KEYS } from '../pages/workspace/stages/review/ShortcutSheet'
import type { Route } from '../router'
import { globalShortcut } from './palette'
import './palette.css'

const PAGE_KEYS: [string, string][] = [
  ['Ctrl/⌘+K', 'Quick search'],
  ['/', 'Search this page (Library, Settings, Sources, Discover)'],
  ['?', 'This list'],
]

const COMIC_KEYS: [string, string][] = [
  ['← / →', 'Previous / next page'],
  ['Home / End', 'First / last page'],
  ['+ / − / 0', 'Zoom in / out / reset'],
  ['Space / Shift+Space', 'Next / previous page (paged mode)'],
]

// The header button and the "?" key share one sheet, so the button asks it to open by event.
const OPEN_EVENT = 'baihe:open-keys'

export function KeyboardHelpButton() {
  return (
    <button
      type="button"
      className="keys-btn"
      aria-label="Keyboard shortcuts"
      aria-haspopup="dialog"
      onClick={() => window.dispatchEvent(new Event(OPEN_EVENT))}
    >
      ?
    </button>
  )
}

function focusPageSearch(): boolean {
  const boxes = document.querySelectorAll<HTMLInputElement>('#main-content input[type="search"]:not([disabled])')
  // A tab that is not shown has no layout box.
  const box = [...boxes].find((el) => el.getClientRects().length > 0)
  box?.focus()
  return !!box
}

export function GlobalShortcuts({ route }: { route: Route }) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener(OPEN_EVENT, show)
    return () => window.removeEventListener(OPEN_EVENT, show)
  }, [])
  useShortcut((combo, { inText }) => {
    // The same guard as the other lists: typing in a field, or a dialog (the palette, a sheet) on top.
    if (inText || document.querySelector('dialog[open]')) return false
    const action = globalShortcut(combo, route)
    if (action === 'sheet') {
      setOpen(true)
      return true
    }
    return action === 'search' && focusPageSearch()
  })
  return (
    <Sheet open={open} title="Keyboard shortcuts" onClose={() => setOpen(false)}>
      <KeyList keys={PAGE_KEYS} />
      <h3>In the comic reader</h3>
      <KeyList keys={COMIC_KEYS} />
      <h3>In Review</h3>
      <KeyList keys={REVIEW_KEYS} />
      <h3>While editing a line</h3>
      <KeyList keys={REVIEW_EDITING_KEYS} />
    </Sheet>
  )
}

function KeyList({ keys }: { keys: [string, string][] }) {
  return (
    <dl className="shortcuts-list">
      {keys.map(([k, v]) => (
        <div key={k}>
          <dt><kbd>{k}</kbd></dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}
