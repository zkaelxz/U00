import { useCallback, useMemo } from 'react'

import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { routeHref } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, type StageId } from './stages'
import { titleKind } from './titleKind'
import { StageContext, type StageContextValue } from './StageContext'
import { SimpleStepNav } from './SimpleStepNav'
import { TitleSteps } from './TitleSteps'
import { useTitleProgress } from './useTitleProgress'
import './titleLines.css'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { TitleView } from './titleView'

// A stage opened from a Simple title page: the stage under a slim header, without the stage tab strip.
export function SimpleStagePage({ id, title, error, refetch, drama, simple, stage, onView }: {
  id: number
  title: string
  error: unknown
  refetch: () => void
  drama: DramaDetail
  simple: boolean
  stage: StageId | null
  onView: (v: TitleView) => void
}) {
  const steps = useTitleProgress(id, false)
  const { refresh } = steps
  // A finished job changes the counts the steps column shows, so it refreshes with the stage.
  const onJobDone = useCallback(() => { refetch(); refresh() }, [refetch, refresh])
  const ctx = useMemo<StageContextValue>(
    () => ({ dramaId: id, drama, refetchDrama: refetch, onJobDone, simple }),
    [id, drama, refetch, onJobDone, simple],
  )
  // `simple` is true only for audio and video, the only kinds whose Simple pages link to Review.
  const Stage = stage ? STAGE_COMPONENTS[stage] : null
  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'drama', id, stage: null })} variant="ghost" size="sm" className="ws-back" aria-label="Back to title page">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">{title}</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>{title}</h1>
        <JobPill dramaId={id} onFinished={refetch} />
        <TitleViewSwitch view="simple" onChange={onView} />
        <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />
        <SourceLink href={drama.source_url} dramaId={id} className={buttonClass('secondary', 'sm', 'ws-source')} />
        {!safeHref(drama.source_url) && (
          <a className="ws-source-add muted" href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Add a source link</a>
        )}
      </header>
      {!simple && <SimpleStepNav id={id} stages={STAGE_IDS.filter((s) => s !== 'review')} active={stage} kind={titleKind(drama.media_type, drama.content_mode)} />}
      <div className={simple ? 'title-studio title-studio-stage' : undefined}>
        <div className="title-studio-main">
          <ErrorBanner error={error} />
          <StageContext.Provider value={ctx}>
            {Stage && <Stage />}
          </StageContext.Provider>
        </div>
        {simple && (
          <div className="title-studio-side">
            <TitleSteps id={id} {...steps} />
            <div className="actions">
              <ButtonLink href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Glossary</ButtonLink>
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
