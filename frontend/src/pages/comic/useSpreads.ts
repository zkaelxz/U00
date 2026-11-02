// Owns the two-page layout of the paged comic reader: whether it is on, what is shown together and where the reader is in it.
import { useMemo } from 'react'

import { useMediaQuery } from '../../hooks/useMediaQuery'
import type { ComicPageInfo } from '../../types/comic'
import { buildSpreads, DOUBLE_QUERY, doubleActive, isWidePage, spreadIndex, spreadLabel, type ComicPrefs } from './comicLogic'

export function useSpreads(prefs: ComicPrefs, visible: number[], pages: ComicPageInfo[], current: number | null) {
  const landscapeWide = useMediaQuery(DOUBLE_QUERY)
  const double = doubleActive(prefs, landscapeWide)
  const spreads = useMemo(
    () => (double ? buildSpreads(visible, (n) => (pages[n - 1] ? isWidePage(pages[n - 1]) : false), prefs.coverAlone) : null),
    [double, visible, pages, prefs.coverAlone],
  )
  const spread = spreads && current !== null ? (spreads[spreadIndex(spreads, current)] ?? [current]) : null
  // Positions in the readable list, for the counter ("3–4 / 20").
  const label = (total: number) => spreadLabel((spread ?? [current ?? 1]).map((n) => visible.indexOf(n) + 1), total)
  // The pager's own text, set only while two pages are on screen.
  const pagerLabel = (total: number) => (spread && spread.length > 1 ? `Pages ${label(total).replace(' / ', ' of ')}` : undefined)
  return { spreads, spread, label, pagerLabel }
}
