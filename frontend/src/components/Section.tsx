/*
 * Section: a collapsible group of settings or records, built on <details>.
 *
 *   <Section storageKey="translate.advanced" title="Advanced"
 *            summary="batch 20 · context 6/3 · no cost cap" count={3}>
 *     ...body...
 *   </Section>
 *
 * Props
 *   title        heading text (required)
 *   summary      one line showing the CURRENT values; visible only while closed
 *   count        optional number shown as a badge next to the title
 *   defaultOpen  initial state when nothing is remembered (default false)
 *   storageKey   remember open/closed per viewer under localStorage
 *                "baihe.section.<storageKey>"; omit to not remember
 *   openSignal   optional number; each time it changes the section opens (lets a
 *                button elsewhere reveal it), without taking control of the state
 *   onToggle     optional; called with the new open state when the viewer
 *                opens or closes it (e.g. to load the body on first open)
 *   children     the body
 *
 * localStorage may throw or be missing (private window, blocked site data);
 * every access is wrapped, and the section then just uses defaultOpen.
 */
import { useState, type ReactNode } from 'react'
import { readSectionOpen, writeSectionOpen, type StorageLike } from './sectionStorage'

function browserStorage(): StorageLike | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

type SectionProps = {
  title: string
  summary?: string
  count?: number
  defaultOpen?: boolean
  storageKey?: string
  onToggle?: (open: boolean) => void
  openSignal?: number
  children: ReactNode
}

export function Section({ title, summary, count, defaultOpen = false, storageKey, onToggle, openSignal, children }: SectionProps) {
  const [open, setOpen] = useState(() =>
    storageKey ? readSectionOpen(browserStorage(), storageKey, defaultOpen) : defaultOpen,
  )

  const [seenSignal, setSeenSignal] = useState(openSignal)
  if (seenSignal !== openSignal) {
    setSeenSignal(openSignal)
    if (!open) setOpen(true)
  }

  return (
    <details
      className="section"
      open={open}
      onToggle={(e) => {
        const next = e.currentTarget.open
        if (next === open) return
        setOpen(next)
        if (storageKey) writeSectionOpen(browserStorage(), storageKey, next)
        onToggle?.(next)
      }}
    >
      <summary>
        <span className="section-title">{title}</span>
        {count !== undefined && <span className="badge section-count">{count}</span>}
        {!open && summary && <span className="section-summary">{summary}</span>}
      </summary>
      <div className="section-body">{children}</div>
    </details>
  )
}
