import { useCallback, useId, useMemo, useState } from 'react'

import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, type StageId } from './stages'
import { titleKind } from './titleKind'
import { StageContext, type StageContextValue } from './StageContext'
import { SimpleStepNav } from './SimpleStepNav'
import { stepPlace, titleSteps, type StepKey } from './stepModel'
import { TitleSteps } from './TitleSteps'
import { useTitleProgress } from './useTitleProgress'
import './titleLines.css'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { TitleView } from './titleView'

const OPEN_STEP: Partial<Record<StageId, StepKey>> = { translate: 'translate', review: 'check', export: 'export' }
const openStep = (stage: StageId | null): StepKey | null => (stage ? OPEN_STEP[stage] ?? null : null)

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
  // Review's writes change the check queue the step's note and button count, so they refresh it as well as the drama.
  const refetchDrama = stage === 'review' ? onJobDone : refetch
  // Pressed on the Review page, the Check button opens the queue there and moves to its first line.
  const [checkSignal, setCheckSignal] = useState(0)
  const openQueue = useCallback(() => setCheckSignal((n) => n + 1), [])
  const ctx = useMemo<StageContextValue>(
    () => ({ dramaId: id, drama, refetchDrama, onJobDone, simple, checkSignal }),
    [id, drama, refetchDrama, onJobDone, simple, checkSignal],
  )
  // `simple` is true only for audio and video, the only kinds whose Simple pages link to Review.
  const Stage = stage ? STAGE_COMPONENTS[stage] : null
  // On a phone Review folds the step tiles and the title's controls into one line, so the check queue's card starts near the top.
  const fold = useMediaQuery('(max-width: 640px)') && simple && stage === 'review'
  const [foldOpen, setFoldOpen] = useState(false)
  const foldId = useId()
  const titleControls = (
    <>
      <TitleViewSwitch view="simple" onChange={onView} />
      <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />
      <SourceLink href={drama.source_url} dramaId={id} className={buttonClass('secondary', 'sm', 'ws-source')} />
      {!safeHref(drama.source_url) && (
        <a className="ws-source-add muted" href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Add a source link</a>
      )}
    </>
  )
  // `simple` is true only for audio and video, the kinds whose Simple pages have the step tabs.
  const stepTiles = simple && <TitleSteps id={id} {...steps} openStep={openStep(stage)} onCheck={stage === 'review' ? openQueue : undefined} />
  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      <header className={fold ? 'workspace-header has-steps-fold' : 'workspace-header'}>
        <ButtonLink href={routeHref({ name: 'drama', id, stage: null })} variant="ghost" size="sm" className="ws-back" aria-label="Back to title page">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">{title}</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>{title}</h1>
        <JobPill dramaId={id} onFinished={refetch} />
        {fold ? (
          <button type="button" className={buttonClass('secondary', undefined, 'steps-fold-toggle')} aria-expanded={foldOpen} aria-controls={foldId} onClick={() => setFoldOpen((o) => !o)}>
            {stepPlace(titleSteps(steps), 'check')}
          </button>
        ) : titleControls}
      </header>
      {!simple && <SimpleStepNav id={id} stages={STAGE_IDS.filter((s) => s !== 'review')} active={stage} kind={titleKind(drama.media_type, drama.content_mode)} />}
      {fold ? (
        <div id={foldId} className="steps-fold-body" hidden={!foldOpen}>
          <div className="steps-fold-controls">{titleControls}</div>
          {stepTiles}
        </div>
      ) : stepTiles}
      <ErrorBanner error={error} />
      <StageContext.Provider value={ctx}>
        {Stage && <Stage />}
      </StageContext.Provider>
    </section>
  )
}
