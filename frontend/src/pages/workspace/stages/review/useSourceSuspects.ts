import { useEffect, useRef, useState } from 'react'

import { listMisheard } from '../../../../api/misheard'
import { listSourceSuspects } from '../../../../api/sourceSuspect'
import type { MisheardSuggestion } from '../../../../types/misheard'
import type { SourceSuspectItem } from '../../../../types/sourceSuspect'
import { useRoute } from '../../../../router'

export interface SourceSuspects {
  items: SourceSuspectItem[]
  misheard: { items: MisheardSuggestion[]; kept: number }
  count: number
}

// A title whose suggestions were all kept still counts one, so the chip stays and "Show kept words
// again" can be reached.
const tally = (lines: number, m: SourceSuspects['misheard']) => lines + m.items.length + (m.items.length === 0 && m.kept > 0 ? 1 : 0)

const NONE: SourceSuspects = { items: [], misheard: { items: [], kept: 0 }, count: 0 }

// The mis-heard scan cuts and compares the whole title, which can take seconds on a long one, and every line
// edit bumps `reloads`. So after the first scan it runs again only while the panel is open, once typing pauses.
const MISHEARD_RESCAN_DEBOUNCE_MS = 800

/** Whether the page was opened by a link from Transcribe's mis-heard note (?focus=source), which talks about this panel. */
export function useOpensSourcePanel(dramaId: number): boolean {
  const route = useRoute()
  return route.name === 'drama' && route.id === dramaId && route.focus === 'source'
}

export interface ScanState {
  dramaId: number
  reloads: number
}

/** How long to wait before scanning for mis-heard words, or null for not now. The first scan of a title is
 *  immediate (the chip needs its count), so is the one when the panel opens on a stale result, and a rescan
 *  while it is open waits for a pause in the edits. A closed panel keeps its old result until opened. */
export function misheardScanDelay(last: ScanState | null, now: ScanState, open: boolean, justOpened: boolean): number | null {
  if (!last || last.dramaId !== now.dramaId) return 0
  if (!open || last.reloads === now.reloads) return null
  return justOpened ? 0 : MISHEARD_RESCAN_DEBOUNCE_MS
}

// The lines flagged "Source text looks off" and the words that may be mis-heard, refetched
// whenever the lines change (the mis-heard scan: see misheardScanDelay). A failed read of either leaves just
// that group out: both are a convenience over what the line rows already show.
export function useSourceSuspects(dramaId: number, reloads: number, open = false): SourceSuspects {
  const [found, setFound] = useState<SourceSuspects>(NONE)
  useEffect(() => {
    let live = true
    // Separate reads: the slower whole-title scan must not hold back the source-suspect chip.
    listSourceSuspects(dramaId).then(
      (r) => live && setFound((f) => ({ ...f, items: r.items, count: tally(r.items.length, f.misheard) })),
      () => live && setFound((f) => ({ ...f, items: [], count: tally(0, f.misheard) })),
    )
    return () => {
      live = false
    }
  }, [dramaId, reloads])

  const scanned = useRef<ScanState | null>(null)
  const wasOpen = useRef(false)
  useEffect(() => {
    const justOpened = open && !wasOpen.current
    wasOpen.current = open
    const now = { dramaId, reloads }
    const delay = misheardScanDelay(scanned.current, now, open, justOpened)
    if (delay === null) return
    let live = true
    const scan = () =>
      listMisheard(dramaId).then(
        (r) => {
          if (!live) return
          scanned.current = now
          setFound((f) => ({ ...f, misheard: { items: r.suggestions, kept: r.kept_count }, count: tally(f.items.length, { items: r.suggestions, kept: r.kept_count }) }))
        },
        () => live && setFound((f) => ({ ...f, misheard: { items: [], kept: 0 }, count: tally(f.items.length, { items: [], kept: 0 }) })),
      )
    const timer = delay === 0 ? null : setTimeout(scan, delay)
    if (timer === null) void scan()
    return () => {
      live = false
      if (timer !== null) clearTimeout(timer)
    }
  }, [dramaId, reloads, open])
  return found
}
