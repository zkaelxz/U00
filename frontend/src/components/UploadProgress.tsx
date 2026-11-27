import { useEffect, useState } from 'react'
import { formatDecimalBytes } from '../pages/diskUsage/diskUsageModel'
import { remainingSeconds, slowUploadHint } from '../pages/workspace/sourceForm'

export type UploadSent = { loaded: number; total: number }

// A big file from away from the PC takes minutes, so show how much has gone and that it is still moving.
function minutesLeft(seconds: number): string {
  return seconds < 90 ? 'about a minute left' : `about ${Math.ceil(seconds / 60)} minutes left`
}

export function UploadProgress({ sent }: { sent: UploadSent | null }) {
  const [startedAt] = useState(() => Date.now())
  const [now, setNow] = useState(startedAt)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 2000)
    return () => clearInterval(timer)
  }, [])
  if (!sent) return <span className="muted" role="status">Starting the upload...</span>
  const pct = Math.min(100, Math.floor((sent.loaded / sent.total) * 100))
  const done = sent.loaded >= sent.total
  const left = remainingSeconds(sent, now - startedAt)
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

// Before a big file starts: how long it may take, so a member on a phone can choose Wi-Fi first.
export function SlowUploadHint({ size }: { size: number }) {
  const hint = slowUploadHint(size)
  return hint ? <span className="muted" data-testid="slow-upload-hint">{hint}</span> : null
}
