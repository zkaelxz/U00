/*
 * "/" and "?" outside the Review editor: "/" focuses the page's search box, "?" opens the list of
 * keys. Review keeps its own "/" and "?" (it has a search box and a longer list), so a title
 * workspace is left alone here rather than having two listeners answer one key.
 */
import { useState } from 'react'

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

function focusPageSearch(): boolean {
  const boxes = document.querySelectorAll<HTMLInputElement>('#main-content input[type="search"]:not([disabled])')
  // A tab that is not shown has no layout box.
  const box = [...boxes].find((el) => el.getClientRects().length > 0)
  box?.focus()
  return !!box
}

export function GlobalShortcuts({ route }: { route: Route }) {
  const [open, setOpen] = useState(false)
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
