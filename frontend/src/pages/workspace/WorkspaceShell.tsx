import { useEffect, useMemo, useRef, useState } from 'react'

import { getWorkflowProgress } from '../../api/workspace'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { isComicType } from '../comic/comicLogic'
import type { WorkflowProgress } from '../../types/workspace'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, STAGE_LABELS, STAGE_STATE_WORDS, type StageId, stageCount, nextAction, stageStates, startStage } from './stages'
import { JobPill } from './JobPill'
import { StageContext, type StageContextValue } from './StageContext'
import { useDrama } from './useDrama'
import './workspace.css'

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
  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(id).then(
      (progress) => !cancelled && setState((s) => ({ progress, opened: s.opened ?? startStage(null, progress, false) })),
      () => !cancelled && setState((s) => ({ progress: s.progress, opened: s.opened ?? startStage(null, null, true) })),
    )
    return () => {
      cancelled = true
    }
  }, [id, reloadKey])
  return state
}

// Tabs that can show a count get its width reserved so the tabs never shift when progress loads.
const COUNTED_STAGES: readonly string[] = ['translate', 'review']

function Workspace({ id, stage }: { id: number; stage: string | null }) {
  const { drama, error, refetch } = useDrama(id)
  const { progress, opened } = useProgress(id, drama)
  const active = stage !== null ? startStage(stage, null, false) : opened
  const states = stageStates(progress?.stages)
  const phone = useMediaQuery('(max-width: 640px)')
  const next = nextAction(active, progress)
  // On Review the phone's fixed edit bar owns the bottom edge, so the bar stays away.
  const nextHref = next ? routeHref({ name: 'drama', id, stage: next.stage }) : null
  const Stage = active ? STAGE_COMPONENTS[active] : null

  const ctx = useMemo<StageContextValue | null>(
    () => (drama ? { dramaId: id, drama, refetchDrama: refetch, onJobDone: refetch } : null),
    [id, drama, refetch],
  )

  // The sticky strip's height is --bar-h, which Review's own sticky toolbar
  // and side card offset from so they sit below the strip, not under it.
  const sectionRef = useRef<HTMLElement>(null)
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const section = sectionRef.current
    const strip = stripRef.current
    if (!section || !strip) return
    const apply = () => section.style.setProperty('--bar-h', `${strip.offsetHeight}px`)
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(strip)
    return () => ro.disconnect()
  }, [])

  const title = drama ? drama.title_en || drama.title_zh || `Drama #${id}` : `Drama #${id}`

  return (
    <section className={`workspace${next && phone && active !== 'review' ? ' has-next-bar' : ''}`} ref={sectionRef} aria-label={`Drama ${id} workspace`}>
      <div className="ws-strip" ref={stripRef}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'library' })} variant="ghost" size="sm" className="ws-back" aria-label="Back to Library">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">Library</span>
        </ButtonLink>
        <h2 data-testid="drama-title" title={title}>
          {title}
        </h2>
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
        {drama && (
          <ButtonLink
            href={routeHref({ name: isComicType(drama.media_type) ? 'comic' : 'read', id, page: null })}
            variant="ghost"
            size="sm"
            className="ws-read"
          >
            Read
          </ButtonLink>
        )}
        {next && nextHref && !phone && (
          <ButtonLink href={nextHref} variant="primary" size="sm" className="ws-next" data-testid="next-action">
            Next: {next.label}
          </ButtonLink>
        )}
      </header>
      <nav className="stage-tabs" aria-label="Stages">
        {STAGE_IDS.map((s) => {
          const st = states[s]
          const count = stageCount(s, progress)
          // The state (and count) is the link's description, not its name, so "Review" stays "Review".
          const desc = [st && STAGE_STATE_WORDS[st], count].filter(Boolean).join(' · ')
          return (
            <a
              key={s}
              href={routeHref({ name: 'drama', id, stage: s })}
              aria-current={s === active ? 'page' : undefined}
              data-state={st}
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
      </div>
      <ErrorBanner error={error} />
      {ctx && Stage ? (
        <StageContext.Provider value={ctx}>
          <Stage />
        </StageContext.Provider>
      ) : (
        !error && (
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
export default function WorkspaceShell({ id, stage }: { id: number; stage: string | null }) {
  return <Workspace key={id} id={id} stage={stage} />
}
