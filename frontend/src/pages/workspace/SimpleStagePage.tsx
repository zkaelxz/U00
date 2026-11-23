import { useCallback, useMemo, useRef, useState } from 'react'

import { OpenFolderButton } from '../../components/OpenFolderButton'
import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { useWantsPhoneBar } from '../../nav/phoneBarRequest'
import { routeHref, useRoute } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, type StageId } from './stages'
import { titleKind } from './titleKind'
import { StageContext, type StageContextValue } from './StageContext'
import { SimpleStepNav } from './SimpleStepNav'
import { EpisodesList } from './EpisodesList'
import { linesOpenOnAll, type StepKey } from './stepModel'
import { TitlePanel } from './TitlePanel'
import { AddSourceLink } from './AddSourceLink'
import { TITLE_TOP_PHONE, TitleMainAction, TitleTop } from './TitleTop'
import { useTitleProgress } from './useTitleProgress'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { QueuePlace } from './stages/review/checkQueueModel'
import type { TitleView } from './titleView'

const OPEN_STEP: Partial<Record<StageId, StepKey>> = { translate: 'translate', review: 'check', export: 'export' }
const openStep = (stage: StageId | null): StepKey | null => (stage ? OPEN_STEP[stage] ?? null : null)
// The source stage is two steps: its Add and Transcribe tabs.
const sourceStep = (tab: string | undefined): StepKey | null => (tab === 'add' || tab === 'transcribe' ? tab : null)

// A stage opened from a Simple title page: the stage under a slim header, without the stage tab strip. With no stage it is
// an audio or video title's own page: Review's lines and tools once the title has lines, else the title panel. One page for
// both, so the header and the one-button flow in it stay put when the first lines arrive.
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
  const home = stage === null
  const steps = useTitleProgress(id, false)
  const { refresh } = steps
  const { lineCount, countFailed, translatedCount, exported, audioReady, riskCount, riskAsked, flowOn, onFlowProgress } = steps
  // The title page waits for the line count before it picks the lines or the title panel, so neither flashes; a count
  // that cannot be read shows the lines, which say for themselves whether there are any.
  const waiting = home && lineCount === null && !countFailed
  const shown: StageId | null = home ? (!waiting && (countFailed || (lineCount ?? 0) > 0) ? 'review' : null) : stage
  const review = shown === 'review'
  // A finished job changes the counts the steps column shows, so it refreshes with the stage.
  const onJobDone = useCallback(() => { refetch(); refresh() }, [refetch, refresh])
  // Review's writes change the check queue the step's note and button count, so they refresh it as well as the drama.
  const refetchDrama = review ? onJobDone : refetch
  // Pressed where the lines are, the Check button opens the queue there and moves to its first line.
  const [checkSignal, setCheckSignal] = useState(0)
  const openQueue = useCallback(() => setCheckSignal((n) => n + 1), [])
  // The header's Search: where the lines are it asks them to show their search box. Elsewhere it goes to Review and leaves
  // a one-shot request there, because a counter would reach the lines already counted as seen when they mount.
  const [searchSignal, setSearchSignal] = useState(0)
  const searchPending = useRef(false)
  const takeSearch = useCallback(() => {
    const asked = searchPending.current
    searchPending.current = false
    return asked
  }, [])
  const onSearch = useCallback(() => {
    if (review) {
      setSearchSignal((n) => n + 1)
      return
    }
    searchPending.current = true
    window.location.hash = routeHref({ name: 'drama', id, stage: 'review' })
  }, [review, id])
  // The Player button is drawn by the lines and shown in the header, so it sits with the title's other controls.
  const [playerDock, setPlayerDock] = useState<HTMLElement | null>(null)
  const phone = useMediaQuery(TITLE_TOP_PHONE)
  useWantsPhoneBar(simple && review && phone)
  // The open card's place in the check queue, for the steps button's "1 of 3 to look at" and its progress bar.
  const [position, setPosition] = useState<QueuePlace | null>(null)
  // Where the lines are, a desktop shows the main button first in the Need a look row, which says when it has drawn it, so the button
  // is never missing and never drawn twice. Without lines it stays here: the one-button flow it may be would restart if moved mid-run.
  const [mainInLead, setMainInLead] = useState(false)
  const leads = simple && review && !phone && !flowOn
  const leadsMain = leads && (lineCount ?? 0) > 0
  // The row's controls are drawn only once the button is final: its place, its words (the Check button counts the lines that
  // need a look, which the queue's size answers on its own) and the pills' position all follow from what is known by then.
  const answered = lineCount !== null || countFailed
  const leadReady = answered && (!leadsMain || riskAsked)
  const route = useRoute()
  const open = stage === 'source' ? sourceStep(route.name === 'drama' ? route.tab : undefined) : openStep(stage)
  const onCheck = review ? openQueue : undefined
  // Built from the counts, not the hook's object, so the stage's context changes only when the button does.
  const checkLead = useMemo(() => {
    if (!leads) return undefined
    const progress = { lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress }
    const action = leadsMain ? <TitleMainAction id={id} progress={progress} onCheck={onCheck} /> : null
    return { action, ready: leadReady, onShown: setMainInLead }
  }, [leads, leadsMain, leadReady, id, lineCount, translatedCount, exported, audioReady, riskCount, flowOn, onFlowProgress, onCheck])
  const linesOnAll = linesOpenOnAll(home, { lineCount, translatedCount, exported, audioReady, riskCount })
  const seriesId = drama.series_id
  const episodes = useMemo(
    () => (simple && review && seriesId != null ? <EpisodesList id={id} drama={drama} lineCount={lineCount} translatedCount={translatedCount} /> : null),
    [simple, review, seriesId, id, drama, lineCount, translatedCount],
  )
  const ctx = useMemo<StageContextValue>(
    () => ({ dramaId: id, drama, refetchDrama, onJobDone, simple, checkSignal, searchSignal, takeSearch, playerDock, checkLead, onPosition: setPosition, linesOnAll, episodes }),
    [id, drama, refetchDrama, onJobDone, simple, checkSignal, searchSignal, takeSearch, playerDock, checkLead, linesOnAll, episodes],
  )
  // `simple` is true only for audio and video, the only kinds whose Simple pages link to Review.
  const Stage = shown ? STAGE_COMPONENTS[shown] : null
  // The title page and Review are where a title opens, so both lead back to the Library; the other steps lead back to them.
  const back = home || stage === 'review'
    ? { href: routeHref({ name: 'library' }), label: 'Back to Library', text: 'Library' }
    : { href: routeHref({ name: 'drama', id, stage: null }), label: 'Back to title page', text: 'Title page' }
  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`} aria-busy={waiting || undefined}>
      {simple ? (
        <TitleTop
          id={id}
          drama={drama}
          title={title}
          back={back}
          badge={home || stage === 'review'}
          home={home}
          onJobFinished={onJobDone}
          onView={onView}
          progress={steps}
          openStep={open}
          onCheck={onCheck}
          onSearch={onSearch}
          playerSlot={setPlayerDock}
          position={position}
          mainElsewhere={leadsMain && mainInLead}
        />
      ) : (
        <>
          <header className="workspace-header">
            <ButtonLink href={routeHref({ name: 'drama', id, stage: null })} variant="ghost" size="sm" className="ws-back" aria-label="Back to title page">
              <span aria-hidden="true">‹</span>
              <span className="ws-back-text">Title page</span>
            </ButtonLink>
            <h1 data-testid="drama-title" title={title}>{title}</h1>
            <JobPill dramaId={id} onFinished={refetch} />
            <TitleViewSwitch view="simple" onChange={onView} />
            <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />
            <SourceLink href={drama.source_url} dramaId={id} className={buttonClass('secondary', 'sm', 'ws-source')} />
            {!safeHref(drama.source_url) && (
              <AddSourceLink id={id} />
            )}
          </header>
          <SimpleStepNav id={id} stages={STAGE_IDS.filter((s) => s !== 'review')} active={stage} kind={titleKind(drama.media_type, drama.content_mode)} />
        </>
      )}
      <ErrorBanner error={error} />
      <StageContext.Provider value={ctx}>
        {Stage && <Stage />}
      </StageContext.Provider>
      {home && !waiting && !Stage && (
        <div className="title-studio">
          <TitlePanel id={id} episodes={seriesId != null ? <EpisodesList id={id} drama={drama} lineCount={lineCount} translatedCount={translatedCount} /> : null} />
        </div>
      )}
    </section>
  )
}
