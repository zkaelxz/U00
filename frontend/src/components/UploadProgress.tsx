import { formatDecimalBytes } from '../pages/diskUsage/diskUsageModel'

export type UploadSent = { loaded: number; total: number }

// A big file from away from the PC takes minutes, so show how much has gone and that it is still moving.
export function UploadProgress({ sent }: { sent: UploadSent | null }) {
  if (!sent) return <span className="muted" role="status">Starting the upload...</span>
  const pct = Math.min(100, Math.floor((sent.loaded / sent.total) * 100))
  const done = sent.loaded >= sent.total
  return (
    <span data-testid="upload-progress" role="status">
      <progress max={sent.total} value={sent.loaded} aria-label="Upload progress" />{' '}
      <span className="muted">
        {done
          ? 'Sent. The PC is saving the file; this can take a minute.'
          : `${pct}% (${formatDecimalBytes(sent.loaded)} of ${formatDecimalBytes(sent.total)}). Still sending; keep this page open.`}
      </span>
    </span>
  )
}
