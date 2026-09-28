import { useMemo } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { routeHref } from '../../router'
import { STAGE_COMPONENTS } from './stageRegistry'
import { STAGE_IDS, STAGE_LABELS, parseStage } from './stages'
import { StageContext, type StageContextValue } from './StageContext'
import { useDrama } from './useDrama'

function Workspace({ id, stage }: { id: number; stage: string }) {
  const { drama, error, refetch } = useDrama(id)
  const active = parseStage(stage)
  const Stage = STAGE_COMPONENTS[active]

  const ctx = useMemo<StageContextValue | null>(
    () => (drama ? { dramaId: id, drama, refetchDrama: refetch, onJobDone: refetch } : null),
    [id, drama, refetch],
  )

  return (
    <section className="workspace" aria-label={`Drama ${id} workspace`}>
      <header className="workspace-header">
        <a href={routeHref({ name: 'library' })}>Back to Library</a>
        <h2 data-testid="drama-title">
          {drama ? drama.title_en || drama.title_zh || `Drama #${id}` : `Drama #${id}`}
        </h2>
        {drama?.status && <span className="badge">{drama.status}</span>}
      </header>
      <ErrorBanner error={error} />
      <nav className="stage-tabs" aria-label="Stages">
        {STAGE_IDS.map((s) => (
          <a
            key={s}
            href={routeHref({ name: 'drama', id, stage: s })}
            aria-current={s === active ? 'page' : undefined}
          >
            {STAGE_LABELS[s]}
          </a>
        ))}
      </nav>
      {ctx ? (
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
export default function WorkspaceShell({ id, stage }: { id: number; stage: string }) {
  return <Workspace key={id} id={id} stage={stage} />
}
