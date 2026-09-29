import { useEffect, useMemo, useState } from 'react'

import { getWorkflowProgress } from '../../api/workspace'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { routeHref } from '../../router'
import { isComicType } from '../comic/comicLogic'
import type { WorkflowProgress } from '../../types/workspace'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, STAGE_LABELS, STAGE_STATE_WORDS, type StageId, stageCount, stageStates, startStage } from './stages'
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

function Workspace({ id, stage }: { id: number; stage: string | null }) {
  const { drama, error, refetch } = useDrama(id)
  const { progress, opened } = useProgress(id, drama)
  const active = stage !== null ? startStage(stage, null, false) : opened
  const states = stageStates(progress?.stages)
  const Stage = active ? STAGE_COMPONENTS[active] : null

  const ctx = useMemo<StageContextValue | null>(
    () => (drama ? { dramaId: id, drama, refetchDrama: refetch, onJobDone: refetch } : null),
    [id, drama, refetch],
  )

  const title = drama ? drama.title_en || drama.title_zh || `Drama #${id}` : `Drama #${id}`

  return (
    <section className="workspace" aria-label={`Drama ${id} workspace`}>
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
      </header>
      <ErrorBanner error={error} />
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
              {st && (
                <span className="stage-mark" aria-hidden="true">
                  {st === 'done' ? '✓' : st === 'current' ? '●' : '○'}
                </span>
              )}
              <span className="stage-label">{STAGE_LABELS[s]}</span>
              {st === 'blocked' && <span className="visually-hidden"> (blocked)</span>}
              {count && (
                <span className="stage-count" aria-hidden="true">
                  · {count}
                </span>
              )}
            </a>
          )
        })}
      </nav>
      {ctx && Stage ? (
        <StageContext.Provider value={ctx}>
          <Stage />
        </StageContext.Provider>
      ) : (
        !error && <p className="muted">Loading…</p>
      )}
    </section>
  )
}

// Keyed by drama id: switching dramas remounts everything below, so no
// stage state (selected file, running job, form values) leaks across dramas.
export default function WorkspaceShell({ id, stage }: { id: number; stage: string | null }) {
  return <Workspace key={id} id={id} stage={stage} />
}
