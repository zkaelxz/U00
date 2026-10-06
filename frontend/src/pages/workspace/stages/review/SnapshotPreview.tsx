import { useEffect, useState } from 'react'

import { getHistorySnapshot } from '../../../../api/review'
import { listAllLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { previewSnapshot, previewSummary, type SnapshotPreview as Preview } from './snapshotPreview'

interface Props {
  dramaId: number
  historyId: number
}

// Read-only: fetches the snapshot and the current lines and compares them
// here; nothing is written until the typed-confirm Restore.
export function SnapshotPreview({ dramaId, historyId }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getHistorySnapshot(dramaId, historyId), listAllLines(dramaId)]).then(
      ([snap, current]) => { if (!cancelled) setPreview(previewSnapshot(current, snap.lines)) },
      (e) => { if (!cancelled) setError(e) },
    )
    return () => { cancelled = true }
  }, [dramaId, historyId])

  if (error) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  if (!preview) return <p className="muted" role="status">Loading preview…</p>
  return (
    <div data-testid="snapshot-preview">
      <p role="status">{previewSummary(preview)}</p>
      {preview.shown.length > 0 && (
        <ul>
          {preview.shown.map((c) => (
            <li key={`${c.number}-${c.field}`}>
              #{c.number} {c.field}: <span className="muted">{c.before || '(empty)'}</span> → {c.after || '(empty)'}
            </li>
          ))}
        </ul>
      )}
      {preview.more > 0 && <p className="muted">…and {preview.more} more edited line{preview.more === 1 ? '' : 's'}.</p>}
    </div>
  )
}
