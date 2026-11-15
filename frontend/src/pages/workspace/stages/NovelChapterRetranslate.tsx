/** "Translate again" for one chapter on the novel title page: preview the lines and cost, then run. */
import { useEffect, useState } from 'react'

import { getChapterRetranslatePreview, startChapterRetranslate } from '../../../api/novelChapters'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import type { NovelChapterRetranslatePreview } from '../../../types/novelChapters'
import { formatCost } from '../../benchmark/benchmarkForm'

interface Props {
  dramaId: number
  number: number
  title: string
  onClose: () => void
  /** Called once the run has started, so the page refreshes its counts. */
  onStarted: () => void
}

export function NovelChapterRetranslate({ dramaId, number, title, onClose, onStarted }: Props) {
  const [preview, setPreview] = useState<NovelChapterRetranslatePreview | null>(null)
  const [withEdited, setWithEdited] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getChapterRetranslatePreview(dramaId, number).then(
      (p) => !cancelled && setPreview(p),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, number])

  const run = async () => {
    if (busy) return
    setBusy(true)
    try {
      await startChapterRetranslate(dramaId, number, withEdited)
      onStarted()
      onClose()
    } catch (e) {
      setError(e)
      setBusy(false)
    }
  }

  const estimate = preview ? (withEdited ? preview.estimate_with_hand_edited : preview.estimate) : null
  const lines = preview ? preview.line_count - (withEdited ? 0 : preview.hand_edited_count) : 0
  return (
    <form
      className="chapters-retranslate"
      aria-label={`Translate chapter ${number} again`}
      onSubmit={(e) => { e.preventDefault(); void run() }}
    >
      <p>
        Translate “{title}” again? {preview ? `${lines.toLocaleString()} of its ${preview.line_count.toLocaleString()} lines` : 'Checking its lines…'}
        {preview ? ' get new English from the engine, glossary and style saved for this title. The old English stays in the line history.' : ''}
      </p>
      {preview && preview.hand_edited_count > 0 && (
        <label>
          <input type="checkbox" checked={withEdited} onChange={(e) => setWithEdited(e.target.checked)} />
          {' '}Include the {preview.hand_edited_count.toLocaleString()} hand-edited {preview.hand_edited_count === 1 ? 'line' : 'lines'}
        </label>
      )}
      {estimate && <p className="muted">{formatCost(estimate.estimated_usd)} at most</p>}
      <div className="actions">
        <button type="submit" className={buttonClass('primary')} disabled={busy || !preview || lines === 0}>Translate again</button>
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={onClose}>Cancel</button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </form>
  )
}
