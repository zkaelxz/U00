import { useState } from 'react'

import { startReviewJob } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { useJob } from '../../../../hooks/useJob'
import type { ReviewJobKind } from '../../../../types/review'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'

const KINDS: { kind: ReviewJobKind; label: string }[] = [
  { kind: 'consistency', label: 'Check consistency' },
  { kind: 'emotion', label: 'Tag emotion' },
  { kind: 'notes', label: 'Generate notes' },
  { kind: 'flag', label: 'Flag lines for a second look' },
  { kind: 'fix-flagged', label: 'Fix flagged lines' },
]

// After every job reaches a terminal state (done, error or cancelled) the
// stage's lines and records are refetched, so translated text and flags never
// stay stale until a hard refresh.
export function ReviewJobsPanel({ dramaId, onChanged }: { dramaId: number; onChanged: () => void }) {
  const { onJobDone } = useStage()
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const { job, done, error: pollError } = useJob(jobId, {
    onDone: () => {
      onJobDone()
      onChanged()
    },
  })
  const busy = jobId !== null && !done && !pollError

  const start = (kind: ReviewJobKind) =>
    startReviewJob(dramaId, kind).then(
      (r) => {
        setError(null)
        setJobId(r.job_id)
      },
      setError,
    )

  return (
    <section className="panel" aria-label="AI checks">
      <h3>AI review</h3>
      <p className="muted">Runs in the background with this drama&apos;s translation engine.</p>
      <div className="review-actions">
        {KINDS.map(({ kind, label }) => (
          <button key={kind} type="button" disabled={busy} onClick={() => start(kind)}>
            {label}
          </button>
        ))}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </section>
  )
}
