import { useEffect, useRef, useState } from 'react'
import { hashWithTab } from './mediaTabs'

interface StageTabSource<T extends string> {
  dramaId: number
  /** The tab the current address names for this title, if any. */
  linked: () => T | undefined
  /** The tab to open on: a link first, then whatever the stage falls back to. */
  initial: () => T | null
  store: (tab: T) => void
}

/** The wanted sub-tab of a stage, kept in step with the address and the per-title stored choice. */
export function useStageTab<T extends string>(source: StageTabSource<T>): [T | null, (tab: T) => void] {
  // Read through a ref so the listener below follows the latest closure without resubscribing on every render.
  const latest = useRef(source)
  latest.current = source
  const [wanted, setWanted] = useState<T | null>(() => source.initial())
  // A link followed while the stage is open (a blocker's "set it in Details") moves the tab too.
  useEffect(() => {
    const follow = () => {
      const next = latest.current.linked()
      if (next) setWanted(next)
    }
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [source.dramaId])
  const select = (next: T) => {
    setWanted(next)
    source.store(next)
    // Keep the address true to the tab shown, so a reload or Back lands here and not on the tab first linked.
    // replaceState sends no hashchange, so this does not come back through the listener above.
    try {
      window.history.replaceState(null, '', hashWithTab(window.location.hash, next))
    } catch {
      // the address stays as it was
    }
  }
  return [wanted, select]
}
