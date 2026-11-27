import { useRef } from 'react'
import { formatDecimalBytes } from '../pages/diskUsage/diskUsageModel'

export type UploadSent = { loaded: number; total: number }

// A big file from away from the PC takes minutes, so show how much has gone and that it is still moving.
// Time left from the speed so far: a measurement, shown only after a few seconds of data.
export function remainingSeconds(sent: UploadSent, elapsedMs: number): number | null {
  if (elapsedMs < 5000 || sent.loaded <= 0 || sent.loaded >= sent.total) return null
  return Math.ceil(((sent.total - sent.loaded) / sent.loaded) * (elapsedMs / 1000))
}

function minutesLeft(seconds: number): string {
  return seconds < 90 ? 'about a minute left' : `about ${Math.ceil(seconds / 60)} minutes left`
}

export function UploadProgress({ sent }: { sent: UploadSent | null }) {
  const startedAt = useRef(Date.now())
  if (!sent) return <span className="muted" role="status">Starting the upload...</span>
  const pct = Math.min(100, Math.floor((sent.loaded / sent.total) * 100))
  const done = sent.loaded >= sent.total
  const left = remainingSeconds(sent, Date.now() - startedAt.current)
  return (
    <span data-testid="upload-progress" role="status">
      <progress max={sent.total} value={sent.loaded} aria-label="Upload progress" />{' '}
      <span className="muted">
        {done
          ? 'Sent. The PC is saving the file; this can take a minute.'
          : `${pct}% (${formatDecimalBytes(sent.loaded)} of ${formatDecimalBytes(sent.total)})${left !== null ? `, ${minutesLeft(left)}` : ''}. Still sending; keep this page open.`}
      </span>
    </span>
  )
}
