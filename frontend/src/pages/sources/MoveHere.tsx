import { useState } from 'react'

import { moveTracked, previewTrackedMove } from '../../api/sources'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { SourceMovePreview, TrackedSeries } from '../../types/sources'

type Props = {
  source: string
  seriesId: string
  title: string
  // Tracked series that can move here, each with its source's display name.
  candidates: { tracked: TrackedSeries; label: string }[]
  onMoved: (list: TrackedSeries[]) => void
}

const CAP = 8

function Names({ items }: { items: { title: string }[] }) {
  if (items.length === 0) return null
  const shown = items.slice(0, CAP).map((c) => c.title || '(no title)')
  return (
    <span className="muted"> {shown.join(', ')}{items.length > CAP ? `, and ${items.length - CAP} more` : ''}</span>
  )
}

/** "Move a tracked title here": compare a tracked series' chapters with the open series, then re-point the link. */
export function MoveHere({ source, seriesId, title, candidates, onMoved }: Props) {
  const [pick, setPick] = useState('')
  const [preview, setPreview] = useState<SourceMovePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const from = candidates.find((c) => `${c.tracked.source}:${c.tracked.series_id}` === pick)?.tracked ?? null

  async function compare() {
    if (!from) return
    setError(null)
    setBusy(true)
    try {
      setPreview(await previewTrackedMove(from, source, seriesId))
    } catch (e) {
      setPreview(null)
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  async function move() {
    if (!from) return
    setError(null)
    setBusy(true)
    try {
      onMoved(await moveTracked(from, source, seriesId))
    } catch (e) {
      setError(e)
      setBusy(false)
    }
  }

  if (candidates.length === 0) return null
  return (
    <div className="sources-move" role="group" aria-label="Move a tracked title here">
      <h3>Move a tracked title here</h3>
      <p className="muted">
        Point a title you already track at this series. Saved chapters and translated pages stay as they are.
      </p>
      <Field label="Tracked title">
        <select
          value={pick}
          disabled={busy}
          onChange={(e) => {
            setPick(e.target.value)
            setPreview(null)
          }}
        >
          <option value="">Pick a tracked title…</option>
          {candidates.map((c) => (
            <option key={`${c.tracked.source}:${c.tracked.series_id}`} value={`${c.tracked.source}:${c.tracked.series_id}`}>
              {c.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={!from || busy} onClick={compare}>
          {busy && !preview ? 'Comparing…' : 'Compare chapters'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
      {preview && from && (
        <div aria-live="polite" data-testid="move-preview">
          <ul>
            <li>
              {preview.matched.length} chapters match and stay as they are.
              <Names items={preview.matched} />
            </li>
            <li>
              {preview.only_old.length} are only on the old source and stay saved.
              <Names items={preview.only_old} />
            </li>
            <li>
              {preview.new.length} are new here. The next check will announce them, and import them if auto-import is on.
              <Names items={preview.new} />
            </li>
          </ul>
          <ConfirmButton
            label="Move here…"
            verb="move"
            name={from.title || title}
            tone="primary"
            busy={busy}
            onConfirm={() => void move()}
          />
        </div>
      )}
    </div>
  )
}
