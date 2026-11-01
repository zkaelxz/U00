import { useEffect, useState } from 'react'

import { listenApi } from '../../api/listen'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useReattachJob } from '../../hooks/useReattachJob'
import { useSession } from '../../hooks/useSession'
import type { ListenConfig } from '../../types/listen'
import { jobFailed, jobSucceeded } from '../../types/jobs'
import { holds } from '../diagnostics/adminUsers'
import { dubJobIds } from '../workspace/stageJobIds'

interface Props {
  dramaId: number
  // The narration track now exists: the Reader re-reads its media so the player appears.
  onReady: () => void
}

// Null when narration can be started, otherwise the plain reason it cannot.
function listenBlocker(cfg: ListenConfig, engine: string): string | null {
  if (cfg.speakable_line_count === 0) return 'There is no text to speak yet.'
  if (cfg.blocker) return cfg.blocker
  if (!engine) return 'No voice engine is available.'
  return cfg.tts_engines.find((t) => t.key === engine)?.unavailable_reason ?? null
}

// Starts the narration job through the Listen routes (one engine, the title's language).
// Only a narrated title with no track yet shows it: the config route is 404 for any other.
export function ListenSection({ dramaId, onReady }: Props) {
  // The server refuses a start without jobs.start; this only hides a button that would lead to that.
  const canStart = holds(useSession(), 'jobs.start')
  const [cfg, setCfg] = useState<ListenConfig | null>(null)

  useEffect(() => {
    let cancelled = false
    listenApi.config(dramaId).then(
      (c) => !cancelled && setCfg(c),
      () => !cancelled && setCfg(null),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (!cfg || !canStart || cfg.track_available) return null
  return <ListenRun dramaId={dramaId} cfg={cfg} onReady={onReady} />
}

// Split from ListenSection so a title that cannot narrate never looks up narration jobs.
function ListenRun({ dramaId, cfg, onReady }: Props & { cfg: ListenConfig }) {
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(dubJobIds(dramaId), adoptJob)

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (jobSucceeded(j)) onReady()
    },
  })

  const busy = jobId !== null && !done && !pollError
  // This run just made the track: the player in Watch / listen takes over.
  if (!busy && done && jobSucceeded(job)) return null
  const engine = cfg.tts_engines.some((t) => t.key === cfg.default_engine) ? cfg.default_engine : (cfg.tts_engines[0]?.key ?? '')
  const blocker = listenBlocker(cfg, engine)
  const failedText = done && job && jobFailed(job) ? job.message : null
  const start = () =>
    listenApi.run(dramaId, { tts_engine: engine, narration_language: cfg.narration_language }).then((r) => {
      setError(null)
      setJobId(r.job_id)
    }, setError)

  return (
    <Section title="Listen" storageKey="reader.listen" defaultOpen summary={busy ? (job?.message ?? '') : undefined}>
      <div className="actions">
        <button type="button" className="primary" disabled={busy || blocker !== null} onClick={start}>
          Listen
        </button>
        {(blocker || busy || failedText) && (
          <span className="muted" role="status">{blocker ?? (busy ? (job?.message ?? '') : failedText)}</span>
        )}
      </div>
      <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
    </Section>
  )
}
