/* Per-page switch between a page's typeset and original image in the reader. */
import { useState } from 'react'

import type { ComicPageInfo } from '../../types/comic'

export function useOriginalPages() {
  const [originals, setOriginals] = useState<ReadonlySet<number>>(new Set())
  const toggle = (page: ComicPageInfo | undefined) => {
    if (!page) return
    setOriginals((o) => {
      const next = new Set(o)
      if (!next.delete(page.id)) next.add(page.id)
      return next
    })
  }
  return { originals, toggle }
}

export function ShowOriginalButton({ page, typeset, originals, onToggle }: { page: ComicPageInfo | undefined; typeset: boolean; originals: ReadonlySet<number>; onToggle: (page: ComicPageInfo | undefined) => void }) {
  return (
    <button type="button" aria-pressed={!!page && originals.has(page.id)} disabled={!page?.has_rendered || !typeset} onClick={() => onToggle(page)}>
      Show Original
    </button>
  )
}
