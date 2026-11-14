import { useEffect, useMemo, useState } from 'react'

import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import { getSeriesPage } from '../../api/series'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { JobPill } from './JobPill'
import { ButtonLink } from '../../components/Button'
import { routeHref } from '../../router'
import { ComicChapterList } from './ComicChapterList'
import { TitleLines, TitleProgress } from './TitleLines'
import { TitlePanel } from './TitlePanel'
import { TitleSteps } from './TitleSteps'
import { useTitleProgress } from './useTitleProgress'
import { TitleViewSwitch } from './TitleViewSwitch'
import { episodeRows, type EpisodeInput, type TitleView } from './titleView'
import './stages/novelChapters.css'
import './titleLines.css'
import './titlePanel.css'

// Audio and video titles in the Simple view: the actions people reach for first and the episodes, with no stepper.
// The stage routes are unchanged and the Full view is one switch away.
export function SimpleTitlePage({ id, drama, comic = false, onView }: { id: number; drama: DramaDetail; comic?: boolean; onView: (v: TitleView) => void }) {
  const { lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress } = useTitleProgress(id, comic)
  const [siblings, setSiblings] = useState<EpisodeInput[] | null>(null)
  // A series page that has not answered yet must not show this title as the only episode.
  const [seriesDone, setSeriesDone] = useState(false)
  const title = drama.title_en || drama.title_zh || `Title #${id}`

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
  const episodesList = (
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

  const hasLines = !comic && (lineCount ?? 0) > 0
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
        <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />
      </header>
      {comic ? (
        <div className="actions">
          <ButtonLink href={routeHref({ name: 'comic', id, page: null })} variant="primary">Open in Scanlate</ButtonLink>
          <ButtonLink href={stage('source', 'add')}>Add pages</ButtonLink>
          <ButtonLink href={stage('translate', 'glossary')}>Glossary</ButtonLink>
          <ButtonLink href={stage('export')}>Export</ButtonLink>
        </div>
      ) : (
        <>
          <TitleSteps
            id={id}
            audioReady={audioReady}
            lineCount={lineCount}
            translatedCount={translatedCount}
            exported={exported}
            riskCount={riskCount}
            flowOn={flowOn}
            onFlowProgress={onFlowProgress}
          />
          <div className={hasLines ? 'title-studio has-panel' : 'title-studio'}>
            {hasLines && (
              <div className="title-studio-main">
                <TitleProgress total={lineCount ?? 0} translated={translatedCount} />
                <TitleLines id={id} total={lineCount ?? 0} />
              </div>
            )}
            <TitlePanel id={id} episodes={episodesList} />
          </div>
        </>
      )}
      {comic && <ComicChapterList id={id} />}
      {comic && (
        <section className="panel" aria-label="Titles in this series">
          <h2 className="source-subhead">Titles in this series</h2>
          {episodesList}
        </section>
      )}
    </section>
  )
}
