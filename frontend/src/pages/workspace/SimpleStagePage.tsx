import { useCallback, useMemo, useState } from 'react'

import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref, useRoute } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, type StageId } from './stages'
import { titleKind } from './titleKind'
import { StageContext, type StageContextValue } from './StageContext'
import { SimpleStepNav } from './SimpleStepNav'
import type { StepKey } from './stepModel'
import { TITLE_TOP_PHONE, TitleMainAction, TitleTop } from './TitleTop'
import { useTitleProgress } from './useTitleProgress'
import './titleLines.css'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { TitleView } from './titleView'

const OPEN_STEP: Partial<Record<StageId, StepKey>> = { translate: 'translate', review: 'check', export: 'export' }
const openStep = (stage: StageId | null): StepKey | null => (stage ? OPEN_STEP[stage] ?? null : null)
// The source stage is two steps: its Add and Transcribe tabs.
const sourceStep = (tab: string | undefined): StepKey | null => (tab === 'add' || tab === 'transcribe' ? tab : null)

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
  const phone = useMediaQuery(TITLE_TOP_PHONE)
  // Desktop Review shows the main button first in its Need a look row, which says when it has drawn it, so the button
  // is never missing and never drawn twice. Without lines it stays here: the one-button flow it may be would restart if moved mid-run.
  const [mainInLead, setMainInLead] = useState(false)
  const { lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress } = steps
  const leadsMain = simple && stage === 'review' && !phone && !flowOn && (lineCount ?? 0) > 0
  const route = useRoute()
  const open = stage === 'source' ? sourceStep(route.name === 'drama' ? route.tab : undefined) : openStep(stage)
  const onCheck = stage === 'review' ? openQueue : undefined
  // Built from the counts, not the hook's object, so the stage's context changes only when the button does.
  const checkLead = useMemo(() => {
    if (!leadsMain) return undefined
    const progress = { lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress }
    return { action: <TitleMainAction id={id} progress={progress} openStep={open} onCheck={onCheck} />, onShown: setMainInLead }
  }, [leadsMain, id, lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress, open, onCheck])
  const ctx = useMemo<StageContextValue>(
    () => ({ dramaId: id, drama, refetchDrama, onJobDone, simple, checkSignal, checkLead }),
    [id, drama, refetchDrama, onJobDone, simple, checkSignal, checkLead],
  )
  // `simple` is true only for audio and video, the only kinds whose Simple pages link to Review.
  const Stage = stage ? STAGE_COMPONENTS[stage] : null
  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      {simple ? (
        <TitleTop
          id={id}
          drama={drama}
          title={title}
          back={{ href: routeHref({ name: 'drama', id, stage: null }), label: 'Back to title page', text: title }}
          onJobFinished={refetch}
          onView={onView}
          progress={steps}
          openStep={open}
          onCheck={onCheck}
          mainElsewhere={leadsMain && mainInLead}
        />
      ) : (
        <>
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
          <SimpleStepNav id={id} stages={STAGE_IDS.filter((s) => s !== 'review')} active={stage} kind={titleKind(drama.media_type, drama.content_mode)} />
        </>
      )}
      <ErrorBanner error={error} />
      <StageContext.Provider value={ctx}>
        {Stage && <Stage />}
      </StageContext.Provider>
    </section>
  )
}
