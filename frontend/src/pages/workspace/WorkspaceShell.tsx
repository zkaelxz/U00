import { useEffect, useMemo, useRef, useState } from 'react'

import { openDramaFolder } from '../../api/library'
import { getWorkflowProgress } from '../../api/workspace'
import { Badge } from '../../components/Badge'
import { Sheet } from '../../components/Sheet'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { RetryBanner } from '../../components/RetryBanner'
import { ApiError } from '../../api/client'
import { OpenFolderButton } from '../../components/OpenFolderButton'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { isComicType } from '../comic/comicLogic'
import type { WorkflowProgress } from '../../types/workspace'
import { COMIC_STAGE_TITLE, ComicStageNotice, isLineStageForComic } from './ComicStageNotice'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, STAGE_LABELS, STAGE_STATE_WORDS, type StageId, stageCount, nextAction, stageStates, startStage } from './stages'
import { OpenInWindowButton } from '../../nav/OpenInWindowButton'
import { isPopOutWindow } from '../../nav/popOut'
import { JobPill } from './JobPill'
import { NovelTitlePage } from './NovelTitlePage'
import { SimpleTitlePage } from './SimpleTitlePage'
import { TitleViewSwitch } from './TitleViewSwitch'
import { TITLE_VIEW_PREF, asTitleView, hasSimpleView, type TitleView } from './titleView'
import { titleKind } from './titleKind'
import { usePersistedState } from '../../hooks/usePersistedState'
import { StageContext, type StageContextValue } from './StageContext'
import { useDrama } from './useDrama'
import './workspace.css'
import { useCancellableEffect } from '../../hooks/useCancellableEffect'

// P16/P17: the drama's pipeline progress, reloaded whenever the drama is
// (after a job finishes or a stage calls refetchDrama). A failure only
// hides the progress marks; the stages still work. `opened` is the stage
// the first answer (or failure) picks for a URL with no stage; it never
// changes afterwards, so a later reload never moves the user.
interface ProgressState {
  progress: WorkflowProgress | null
  opened: StageId | null
}

function useProgress(id: number, reloadKey: unknown) {
  const [state, setState] = useState<ProgressState>({ progress: null, opened: null })
  useCancellableEffect((cancelled) => {
    getWorkflowProgress(id).then(
      (progress) => !cancelled() && setState((s) => ({ progress, opened: s.opened ?? startStage(null, progress, false) })),
      () => !cancelled() && setState((s) => ({ progress: s.progress, opened: s.opened ?? startStage(null, null, true) })),
    )
  }, [id, reloadKey])
  return state
}

// Tabs that can show a count get its width reserved so the tabs never shift when progress loads.
const COUNTED_STAGES: readonly string[] = ['translate', 'review']

function Workspace({ id, stage }: { id: number; stage: string | null }) {
  const { drama, error, refetch } = useDrama(id)
  // A load that never answers must end in Retry, not an endless skeleton.
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    if (drama || error) return
    const t = setTimeout(() => setSlow(true), SLOW_LOAD_MS)
    return () => clearTimeout(t)
  }, [id, drama, error])
  const { progress, opened } = useProgress(id, drama)
  const active = stage !== null ? startStage(stage, null, false) : opened
  const states = stageStates(progress?.stages)
  const phone = useMediaQuery('(max-width: 640px)')
  const [moreOpen, setMoreOpen] = useState(false)
  const comic = !!drama && isComicType(drama.media_type)
  const [storedView, storeView] = usePersistedState<string>(TITLE_VIEW_PREF, 'full')
  const twoViews = !!drama && hasSimpleView(titleKind(drama.media_type, drama.content_mode))
  const view = asTitleView(storedView)
  // Simple is a title page like the novel's, so a link that names a stage always opens the stage: choosing Simple there goes to the page itself.
  const chooseView = (v: TitleView) => {
    storeView(v)
    if (v === 'simple' && stage !== null) window.location.hash = routeHref({ name: 'drama', id, stage: null })
  }
  const comicNotice = comic && isLineStageForComic(active)
  // The line stages are empty for a comic, so "Next" never sends it there.
  const rawNext = nextAction(active, progress)
  // With every line translated the Translate stage already offers Next: Review beside its reason; a second one here would repeat it.
  const stageOffersNext = active === 'translate' && rawNext?.stage === 'review' && !!progress && progress.line_count > 0 && progress.untranslated_count <= 0
  const next = (comic && rawNext && isLineStageForComic(rawNext.stage)) || stageOffersNext ? null : rawNext
  // On Review the phone's fixed edit bar owns the bottom edge, so the bar stays away.
  const nextHref = next ? routeHref({ name: 'drama', id, stage: next.stage }) : null
  const Stage = active ? STAGE_COMPONENTS[active] : null

  const ctx = useMemo<StageContextValue | null>(
    () => (drama ? { dramaId: id, drama, refetchDrama: refetch, onJobDone: refetch } : null),
    [id, drama, refetch],
  )

  // --bar-h is the app header plus this sticky strip, which Review's own sticky
  // toolbar and side card offset from so they sit below both, not under them.
  const sectionRef = useRef<HTMLElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const section = sectionRef.current
    const strip = stripRef.current
    if (!section || !strip) return
    const apply = () => section.style.setProperty('--bar-h', `calc(env(safe-area-inset-top, 0px) + var(--app-header-h, 0px) + ${strip.offsetHeight}px)`)
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(strip)
    return () => ro.disconnect()
  }, [])

  const title = drama ? drama.title_en || drama.title_zh || `Title #${id}` : `Title #${id}`

  // On a phone the title row has room for one button only, so these sit behind "More". Review leaves them out:
  // its first screen is for lines.
  const actions = (
    <div className="ws-actions">
      <SourceLink href={drama?.source_url} dramaId={id} className={buttonClass('secondary', 'sm', 'ws-source')} />
      {drama && !safeHref(drama.source_url) && (
        <a className="ws-source-add muted" href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Add a source link</a>
      )}
      {drama && (
        <ButtonLink
          href={routeHref({ name: isComicType(drama.media_type) ? 'comic' : 'read', id, page: null })}
          variant="secondary"
          size="sm"
          className="ws-read"
          data-testid="header-open"
        >
          {comic ? 'Open in Scanlate' : 'Read'}
        </ButtonLink>
      )}
      {drama && <OpenFolderButton open={() => openDramaFolder(id)} size="sm" className="ws-folder" title="Show this title's files on the PC" />}
      {drama && !isPopOutWindow() && <OpenInWindowButton />}
    </div>
  )
  // The phone stepper drops the counts and shows only a marker, so the open stage's state is spelled out below it.
  const activeState = active ? states[active] : undefined
  const activeDesc = active ? [activeState && STAGE_STATE_WORDS[activeState], stageCount(active, progress)].filter(Boolean).join(' · ') : ''
  if (stage === null && drama?.media_type === 'novel') return <NovelTitlePage id={id} drama={drama} />
  if (stage === null && drama && twoViews && view === 'simple') return <SimpleTitlePage id={id} drama={drama} onView={chooseView} />
  return (
    <section className={`workspace${next && phone && active !== 'review' ? ' has-next-bar' : ''}`} ref={sectionRef} aria-label={`Title ${id} workspace`}>
      <div className="ws-strip" ref={stripRef}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'library' })} variant="ghost" size="sm" className="ws-back" aria-label="Back to Library">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">Library</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>
          {title}
        </h1>
        <div className="ws-badges">
          {drama?.status && <Badge kind="status" value={drama.status} />}
          {drama?.media_type && (
            <span className="ws-media">
              <Badge kind="mediaType" value={drama.media_type} />
            </span>
          )}
          {progress && progress.line_count > 0 && (
            <span className="ws-lines muted" data-testid="stage-counts">
              {progress.line_count} lines
            </span>
          )}
        </div>
        <JobPill dramaId={id} onFinished={refetch} />
        {twoViews && !phone && <TitleViewSwitch view={view} onChange={chooseView} />}
        {!phone && actions}
        {phone && active !== 'review' && (
          <button type="button" className={buttonClass('ghost', 'sm', 'ws-more')} aria-label="Title actions" onClick={() => setMoreOpen(true)}>
            <span aria-hidden="true">⋯</span>
          </button>
        )}
      </header>
      <div className="stage-row">
      <nav className="stage-tabs" aria-label="Stages">
        {STAGE_IDS.map((s) => {
          const st = states[s]
          const count = stageCount(s, progress)
          // The state (and count) is the link's description, not its name, so "Review" stays "Review".
          const skipped = comic && isLineStageForComic(s)
          const desc = skipped ? COMIC_STAGE_TITLE : [st && STAGE_STATE_WORDS[st], count].filter(Boolean).join(' · ')
          return (
            <a
              key={s}
              href={routeHref({ name: 'drama', id, stage: s })}
              aria-current={s === active ? 'page' : undefined}
              data-state={st}
              data-comic-skipped={skipped ? 'true' : undefined}
              title={desc ? `${STAGE_LABELS[s]}: ${desc}` : undefined}
            >
              {st ? (
                <span className="stage-mark" aria-hidden="true">
                  {st === 'done' ? '✓' : st === 'current' ? '●' : '○'}
                </span>
              ) : (
                <span className="stage-mark-slot" aria-hidden="true">○</span>
              )}
              <span className="stage-label" data-label={STAGE_LABELS[s]}>{STAGE_LABELS[s]}</span>
              {st === 'blocked' && <span className="visually-hidden"> (blocked)</span>}
              {count ? (
                <span className="stage-count" aria-hidden="true">
                  · {count}
                </span>
              ) : (
                COUNTED_STAGES.includes(s) && <span className="stage-count stage-count-slot" aria-hidden="true" />
              )}
            </a>
          )
        })}
      </nav>
      {next && nextHref && !phone && (
        <a href={nextHref} className="ws-next-link" data-testid="next-action">
          Next: {next.label}
        </a>
      )}
      </div>
      </div>
      <Sheet open={phone && moreOpen} title="Title actions" hideTitle onClose={() => setMoreOpen(false)}>
        <div onClick={() => setMoreOpen(false)}>
          {twoViews && <TitleViewSwitch view={view} onChange={chooseView} />}
          {actions}
        </div>
      </Sheet>
      {phone && active && activeDesc && <p className="ws-stage-state muted" data-testid="stage-state">{STAGE_LABELS[active]}: {activeDesc}</p>}
      {drama ? (
        <ErrorBanner error={error} />
      ) : (
        <RetryBanner error={error ?? (slow ? new ApiError(0, { code: 'timeout', message: 'No answer.' }, 'GET') : null)} onRetry={refetch} />
      )}
      {comicNotice ? (
        <ComicStageNotice id={id} />
      ) : ctx && Stage ? (
        <StageContext.Provider value={ctx}>
          <Stage />
        </StageContext.Provider>
      ) : (
        !error && !slow && (
          <div className="skeleton-block ws-skeleton" role="status" aria-busy="true">
            <span className="visually-hidden">Loading…</span>
          </div>
        )
      )}
      {next && nextHref && phone && active !== 'review' && (
        <div className="ws-next-bar">
          <ButtonLink href={nextHref} variant="primary" className="ws-next" data-testid="next-action">
            Next: {next.label}
          </ButtonLink>
        </div>
      )}
    </section>
  )
}

// Keyed by drama id: switching dramas remounts everything below, so no
// stage state (selected file, running job, form values) leaks across dramas.
const SLOW_LOAD_MS = 10_000

export default function WorkspaceShell({ id, stage }: { id: number; stage: string | null }) {
  return <Workspace key={id} id={id} stage={stage} />
}
