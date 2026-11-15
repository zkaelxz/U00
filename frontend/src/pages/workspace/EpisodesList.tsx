/** The list of a title's series with how far each title is, shared by the Simple title pages. */
import { useEffect, useMemo, useState } from 'react'

import { getSeriesPage } from '../../api/series'
import type { DramaDetail } from '../../api/types'
import { routeHref } from '../../router'
import { episodeRows, type EpisodeInput } from './titleView'
import './stages/novelChapters.css'

export function EpisodesList({ id, drama, comic = false, lineCount, translatedCount }: {
  id: number
  drama: DramaDetail
  comic?: boolean
  /** Null until known. */
  lineCount: number | null
  translatedCount: number
}) {
  const [siblings, setSiblings] = useState<EpisodeInput[] | null>(null)
  // A series page that has not answered yet must not show this title as the only episode.
  const [seriesDone, setSeriesDone] = useState(false)

  // A title in a series lists its episodes; a failed or missing series page leaves just this title, which is still correct.
  const seriesId = drama.series_id
  useEffect(() => {
    if (seriesId == null) return
    let cancelled = false
    getSeriesPage(seriesId).then(
      (s) => {
        if (cancelled) return
        setSiblings(s.titles)
        setSeriesDone(true)
      },
      () => !cancelled && setSeriesDone(true),
    )
    return () => {
      cancelled = true
    }
  }, [seriesId])

  const rows = useMemo(() => {
    // A comic has no transcript, so "Not transcribed" would be wrong for every chapter.
    if (siblings && siblings.some((t) => t.id === id)) return episodeRows(siblings).map((r) => (comic ? { ...r, state: '' } : r))
    const own: EpisodeInput = {
      id, title_en: drama.title_en, title_zh: drama.title_zh, episode_number: drama.episode_number ?? null,
      line_count: lineCount ?? 0, translated_count: translatedCount,
    }
    // No state words until the counts are known: "Not transcribed" before they arrive would be wrong for a transcribed title.
    const rows = episodeRows([own])
    return lineCount === null || comic ? rows.map((r) => ({ ...r, state: '' })) : rows
  }, [comic, siblings, id, drama.title_en, drama.title_zh, drama.episode_number, lineCount, translatedCount])

  const loadingSeries = seriesId != null && !seriesDone
  return (
    <ul className="chapters-list episodes-list" aria-label={comic ? 'Titles in this series' : 'Episodes'} aria-busy={loadingSeries || lineCount === null}>
      {rows.map((r) => (
        <li key={r.id} className="chapters-item">
          <a className="chapters-row episodes-row" href={routeHref({ name: 'drama', id: r.id, stage: null })} aria-current={r.id === id ? 'page' : undefined}>
            <span className="chapters-title">{r.title}</span>
            <span className="chapters-meta muted">{r.state}</span>
          </a>
        </li>
      ))}
    </ul>
  )
}
