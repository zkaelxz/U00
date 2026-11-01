/* Which reader pages the viewer switched to their original image. */
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
