/** The Simple view's check queue around the line list: the Need a look | All switch, the footer under the cards and the Undo of the last line checked. */
import { useId, useState } from 'react'

import { markEpisodeReviewed } from '../../../../api/review'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { HelpTip } from '../../../../components/HelpTip'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import {
  checkedText, fineText, markRestBlocker, otherFineText, restUnchecked, untranslatedText, type QueueCounts,
} from './checkQueueModel'
import { MARK_REVIEWED_HELP, markEpisodeReviewedText } from './markEpisodeReviewedText'

export function CheckQueueSwitch({ queue, counts, onPick, showFine = true }: {
  queue: boolean
  counts: QueueCounts | null
  onPick: (queue: boolean) => void
  showFine?: boolean
}) {
  const helpId = useId()
  const fine = counts && showFine ? fineText(counts) : null
  return (
    <>
      <fieldset className="segmented check-queue-switch">
        <legend className="visually-hidden">Lines to show</legend>
        <label>
          <input type="radio" name="check-queue" checked={queue} onChange={() => onPick(true)} />
          <span>{counts ? `Need a look ${counts.risk}` : 'Need a look'}</span>
        </label>
        <label>
          <input type="radio" name="check-queue" checked={!queue} onChange={() => onPick(false)} />
          <span>{counts?.all != null ? `All ${counts.all}` : 'All'}</span>
        </label>
      </fieldset>
      {fine && <span className="muted check-queue-fine" data-testid="queue-fine">{fine}</span>}
      <HelpTip label="Why these lines" id={helpId}>
        A line is listed when a check flagged it or Whisper was unsure of it, and nobody has marked it checked yet. The
        riskiest come first. More checks are under Look for problems.
      </HelpTip>
    </>
  )
}

// Under the cards: what the rest of the title looks like, and the one bulk step, which also teaches translation memory.
export function CheckQueueFooter({ dramaId, queuePage, counts, jobRunning, settled, onReadAll, onUntranslated, onChanged }: {
  dramaId: number
  queuePage: ReviewLinesPage
  counts: QueueCounts
  jobRunning: boolean
  // False while a reload is on its way: the page shown may miss a line that now needs a look.
  settled: boolean
  onReadAll: () => void
  onUntranslated: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState<string | null>(null)
  const reasonId = useId()
  const helpId = useId()
  const other = otherFineText(counts)
  const rest = restUnchecked(counts) > 0
  const blocker = markRestBlocker(queuePage, jobRunning)
  const untranslated = untranslatedText(counts.untranslated)
  if (!other && !rest && !untranslated && !note && !error) return null

  const run = () => {
    setError(null)
    setNote(null)
    setBusy(true)
    markEpisodeReviewed(dramaId)
      .then((r) => {
        setNote(markEpisodeReviewedText(r))
        onChanged()
      }, setError)
      .finally(() => setBusy(false))
  }

  return (
    <section className="check-queue-footer" aria-label="The other lines" data-testid="queue-footer">
      {other && <p>{other}</p>}
      {(counts.risk > 0 || rest) && (
        <div className="check-queue-actions">
          {counts.risk > 0 && <button type="button" className={buttonClass('secondary', 'sm')} onClick={onReadAll}>Read them all</button>}
          {rest && (
            <span className="check-queue-mark">
              <ConfirmButton
                name="the rest"
                label="Mark the rest as checked…"
                ariaLabel="Mark the rest as checked"
                confirmLabel="Confirm: record untouched lines as approved"
                verb="mark checked"
                tone="primary"
                size="sm"
                busy={busy}
                disabled={blocker !== null || !settled}
                describedBy={blocker ? reasonId : undefined}
                onConfirm={run}
              />
              <HelpTip label="Mark the rest as checked" id={helpId}>{MARK_REVIEWED_HELP}</HelpTip>
            </span>
          )}
        </div>
      )}
      {rest && blocker && <p className="muted" id={reasonId} data-testid="mark-rest-reason">{blocker}</p>}
      {untranslated && (
        <p className="check-queue-untranslated">
          {untranslated}{' '}
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={onUntranslated}>Show the untranslated lines</button>
        </p>
      )}
      {note && <p className="muted" role="status">{note}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}

export function CheckedUndo({ line, busy, onUndo, onDismiss }: {
  line: ReviewLine
  busy: boolean
  onUndo: () => void
  onDismiss: () => void
}) {
  return (
    <div className="check-queue-undo" role="status" data-testid="checked-undo">
      <span>{checkedText(line)}</span>
      <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={onUndo}>Undo</button>
      <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Dismiss" onClick={onDismiss}>×</button>
    </div>
  )
}
