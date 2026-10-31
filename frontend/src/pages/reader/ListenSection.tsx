import { useEffect, useState } from 'react'

import { listenApi } from '../../api/listen'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useReattachJob } from '../../hooks/useReattachJob'
import type { ListenConfig } from '../../types/listen'
import { jobFailed, jobSucceeded } from '../../types/jobs'
import { dubJobIds } from '../workspace/stageJobIds'

// Null when narration can be started, otherwise the plain reason it cannot.
function listenBlocker(cfg: ListenConfig, engine: string): string | null {
  if (cfg.speakable_line_count === 0) return 'There is no text to speak yet.'
  if (cfg.blocker) return cfg.blocker
  if (!engine) return 'No voice engine is available.'
  return cfg.tts_engines.find((t) => t.key === engine)?.unavailable_reason ?? null
}

interface Props {
  dramaId: number
  // The narration track now exists: the Reader re-reads its media so the player appears.
  onReady: () => void
}

// Starts the narration job through the Listen routes (one engine, the title's language).
// Only a narrated title with no track yet shows it: the config route is 404 for any other.
export function ListenSection({ dramaId, onReady }: Props) {
  const [cfg, setCfg] = useState<ListenConfig | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(dubJobIds(dramaId), adoptJob)

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

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (jobSucceeded(j)) onReady()
    },
  })

  const busy = jobId !== null && !done && !pollError
  // The track exists (found on load, or this run just made it): the player in Watch / listen takes over.
  if (!cfg || (!busy && (cfg.track_available || (done && jobSucceeded(job))))) return null
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
