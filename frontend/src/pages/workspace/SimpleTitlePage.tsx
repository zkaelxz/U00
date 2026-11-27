import { useState } from 'react'

import { nameLang } from '../../hanScript'
import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { JobPill } from './JobPill'
import { ButtonLink } from '../../components/Button'
import { routeHref } from '../../router'
import { ComicChapterList } from './ComicChapterList'
import { EpisodesList } from './EpisodesList'
import { useTitleProgress } from './useTitleProgress'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { TitleView } from './titleView'

// A comic in the Simple view: Scanlate and the pages first, its chapters and the series' titles, with no stepper.
// The stage routes are unchanged and the Full view is one switch away.
export function SimpleTitlePage({ id, drama, onView }: { id: number; drama: DramaDetail; onView: (v: TitleView) => void }) {
  const { lineCount, translatedCount } = useTitleProgress(id, true)
  const [jobsEnded, setJobsEnded] = useState(0)
  const title = drama.title_en || drama.title_zh || `Title #${id}`
  const stage = (s: string, tab?: 'add' | 'glossary') => routeHref({ name: 'drama', id, stage: s, ...(tab ? { tab } : {}) })

  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'library' })} variant="ghost" size="sm" className="ws-back" aria-label="Back to Library">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">Library</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title} lang={nameLang(title, drama)}>{title}</h1>
        <div className="ws-badges">
          <Badge kind="mediaType" value={drama.media_type} />
        </div>
        <JobPill dramaId={id} onFinished={() => setJobsEnded((n) => n + 1)} />
        <TitleViewSwitch view="simple" onChange={onView} />
        <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />
      </header>
      <div className="actions title-actions">
        <ButtonLink href={routeHref({ name: 'comic', id, page: null })} variant="primary">Open in Scanlate</ButtonLink>
        <ButtonLink href={stage('source', 'add')}>Add pages</ButtonLink>
        <ButtonLink href={stage('translate', 'glossary')}>Glossary</ButtonLink>
        <ButtonLink href={stage('export')}>Export</ButtonLink>
      </div>
      <ComicChapterList id={id} refresh={jobsEnded} />
      <section className="panel" aria-label="Titles in this series">
        <h2 className="source-subhead">Titles in this series</h2>
        <EpisodesList id={id} drama={drama} comic lineCount={lineCount} translatedCount={translatedCount} />
      </section>
    </section>
  )
}
