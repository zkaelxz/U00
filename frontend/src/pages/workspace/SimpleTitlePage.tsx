import { useEffect, useMemo, useState } from 'react'

import { getSeriesPage } from '../../api/series'
import { getWorkflowProgress } from '../../api/workspace'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { JobPill } from './JobPill'
import { ButtonLink } from '../../components/Button'
import { routeHref } from '../../router'
import { TitleViewSwitch } from './TitleViewSwitch'
import { episodeRows, type EpisodeInput, type TitleView } from './titleView'
import './stages/novelChapters.css'

// Audio and video titles in the Simple view: the actions people reach for first and the episodes, with no stepper.
// The stage routes are unchanged and the Full view is one switch away.
export function SimpleTitlePage({ id, drama, onView }: { id: number; drama: DramaDetail; onView: (v: TitleView) => void }) {
  // Unknown until answered; with no lines yet, the main button starts at Transcribe.
  const [lineCount, setLineCount] = useState<number | null>(null)
  const [translatedCount, setTranslatedCount] = useState(0)
  const [siblings, setSiblings] = useState<EpisodeInput[] | null>(null)
  // A series page that has not answered yet must not show this title as the only episode.
  const [seriesDone, setSeriesDone] = useState(false)
  const title = drama.title_en || drama.title_zh || `Title #${id}`

  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(id).then(
      (p) => {
        if (cancelled) return
        setLineCount(p.line_count)
        setTranslatedCount(p.line_count - p.untranslated_count)
      },
      () => !cancelled && setLineCount(null),
    )
    return () => {
      cancelled = true
    }
  }, [id])

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
    if (siblings && siblings.some((t) => t.id === id)) return episodeRows(siblings)
    const own: EpisodeInput = {
      id, title_en: drama.title_en, title_zh: drama.title_zh, episode_number: drama.episode_number ?? null,
      line_count: lineCount ?? 0, translated_count: translatedCount,
    }
    // No state words until the counts are known: "Not transcribed" before they arrive would be wrong for a transcribed title.
    const rows = episodeRows([own])
    return lineCount === null ? rows.map((r) => ({ ...r, state: '' })) : rows
  }, [siblings, id, drama.title_en, drama.title_zh, drama.episode_number, lineCount, translatedCount])

  const loadingSeries = seriesId != null && !seriesDone
  const hasLines = lineCount !== null && lineCount > 0
  const stage = (s: string, tab?: 'add' | 'transcribe' | 'glossary') => routeHref({ name: 'drama', id, stage: s, ...(tab ? { tab } : {}) })

  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'library' })} variant="ghost" size="sm" className="ws-back" aria-label="Back to Library">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">Library</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>{title}</h1>
        <div className="ws-badges">
          <Badge kind="mediaType" value={drama.media_type} />
        </div>
        <JobPill dramaId={id} onFinished={() => undefined} />
        <TitleViewSwitch view="simple" onChange={onView} />
      </header>
      <div className="actions">
        <ButtonLink href={hasLines ? stage('translate') : stage('source', 'transcribe')} variant="primary">Transcribe and translate</ButtonLink>
        <ButtonLink href={stage('source', 'add')}>Add media</ButtonLink>
        <ButtonLink href={stage('translate', 'glossary')}>Glossary</ButtonLink>
        <ButtonLink href={stage('review')}>Review</ButtonLink>
        <ButtonLink href={stage('export')}>Export</ButtonLink>
      </div>
      <section className="panel" aria-label="Episodes">
        <h2 className="source-subhead">Episodes</h2>
        <ul className="chapters-list episodes-list" aria-label="Episodes" aria-busy={loadingSeries || lineCount === null}>
          {rows.map((r) => (
            <li key={r.id} className="chapters-item">
              <a className="chapters-row episodes-row" href={routeHref({ name: 'drama', id: r.id, stage: null })} aria-current={r.id === id ? 'page' : undefined}>
                <span className="chapters-title">{r.title}</span>
                <span className="chapters-meta muted">{r.state}</span>
              </a>
            </li>
          ))}
        </ul>
      </section>
    </section>
  )
}
