import { useMemo } from 'react'

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
import { StageContext, type StageContextValue } from './StageContext'
import { SimpleStepNav } from './SimpleStepNav'
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
  const ctx = useMemo<StageContextValue>(
    () => ({ dramaId: id, drama, refetchDrama: refetch, onJobDone: refetch, simple }),
    [id, drama, refetch, simple],
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
        <SourceLink href={drama.source_url} dramaId={id} className={buttonClass('secondary', 'sm', 'ws-source')} />
        {!safeHref(drama.source_url) && (
          <a className="ws-source-add muted" href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Add a source link</a>
        )}
      </header>
      <SimpleStepNav id={id} stages={STAGE_IDS.filter((s) => simple || s !== 'review')} active={stage} />
      <ErrorBanner error={error} />
      <StageContext.Provider value={ctx}>
        {Stage && <Stage />}
      </StageContext.Provider>
    </section>
  )
}
