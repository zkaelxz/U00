/** The Simple view's check queue around the line list: the Need a look | All switch, the footer under the cards and the Undo of the last line checked. */
import { useId, useLayoutEffect, useState } from 'react'

import { markEpisodeReviewed } from '../../../../api/review'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { HelpTip } from '../../../../components/HelpTip'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import type { CheckLead } from '../../StageContext'
import {
  askedReason, checkedText, fineText, markRestBlocker, markRestView, otherFineText, restUnchecked, untranslatedText, type QueueCounts,
} from './checkQueueModel'
import { MARK_REVIEWED_HELP, markEpisodeReviewedText } from './markEpisodeReviewedText'

// The title's main button, first in the switch row. A layout effect, so the page drops its own copy before the browser paints both.
// The row is drawn only once the button is known (`ready`): a button that arrived after the pills would push them aside.
export function CheckLeadAction({ lead }: { lead: CheckLead }) {
  const { onShown, action } = lead
  const claims = action !== null
  useLayoutEffect(() => {
    if (!claims) return
    onShown(true)
    return () => onShown(false)
  }, [onShown, claims])
  return lead.ready ? <>{action}</> : null
}

// Simple folds an audio title's player away, in Need a look and under All, so the first line sits under the switch; this brings it back.
// It sits in the header's More row beside buttons of the same kind, so it is one of them in size.
export function QueueMediaToggle({ open, controls, onToggle }: { open: boolean; controls: string; onToggle: () => void }) {
  return (
    <button type="button" className={buttonClass('secondary', 'md', 'check-queue-media')} aria-expanded={open} aria-controls={controls}
            title="Seek bar, speed, subtitles and the waveform" onClick={onToggle}>
      Player <span aria-hidden="true">{open ? '▴' : '▾'}</span>
    </button>
  )
}

export function CheckQueueSwitch({ queue, counts, onPick }: {
  queue: boolean
  counts: QueueCounts | null
  onPick: (queue: boolean) => void
}) {
  return (
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
  )
}

// What the rest of the title looks like, at the far end of the switch row, as one group with its (i).
export function QueueFine({ counts }: { counts: QueueCounts | null }) {
  const fine = counts ? fineText(counts) : null
  return (
    <span className="check-queue-fine-group">
      {fine && <span className="muted check-queue-fine" data-testid="queue-fine">{fine}</span>}
      <QueueWhyHelp />
    </span>
  )
}

// The (i) saying why lines are listed: beside "look fine" on a wide screen, beside the footer's sentence on a phone.
function QueueWhyHelp() {
  const helpId = useId()
  return (
    <HelpTip label="Why these lines" id={helpId}>
      A line is listed when a check flagged it or Whisper was unsure of it, and nobody has marked it checked yet. The
      riskiest come first. More checks are under Look for problems.
    </HelpTip>
  )
}

// Under the cards: what the rest of the title looks like, and the one bulk step, which also teaches translation memory.
export function CheckQueueFooter({ dramaId, queuePage, counts, jobRunning, settled, why = false, onReadAll, onUntranslated, onChanged }: {
  dramaId: number
  queuePage: ReviewLinesPage
  counts: QueueCounts
  jobRunning: boolean
  // False while a reload is on its way: the page shown may miss a line that now needs a look.
  settled: boolean
  // A phone has no "look fine" row above the card, so the footer's sentence carries the (i) that row would.
  why?: boolean
  onReadAll: () => void
  onUntranslated: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState<string | null>(null)
  const [askedFor, setAskedFor] = useState<string | null>(null)
  const reasonId = useId()
  const helpId = useId()
  const other = otherFineText(counts)
  const rest = restUnchecked(counts) > 0
  const blocker = markRestBlocker(queuePage, jobRunning)
  const untranslated = untranslatedText(counts.untranslated)
  // A reason asked for stays on screen only while it is the one that blocks; another reason, or the same one after it lifted, is not shown before its own press.
  const asked = askedReason(askedFor, blocker)
  if (askedFor !== asked) setAskedFor(asked)
  const markRest = markRestView(blocker, asked !== null, settled)
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
      <div className="check-queue-line">
        {other && <span>{other}</span>}
        {why && <QueueWhyHelp />}
        {counts.risk > 0 && <button type="button" className="check-queue-link" onClick={onReadAll}>Read them all</button>}
        {rest && (
          <span className="check-queue-mark">
            {/* While it cannot run it is still a link: pressing it says why, instead of a greyed button with the reason always on screen. */}
            {markRest.control === 'say-why' ? (
              <button type="button" className="check-queue-link" disabled={markRest.disabled} aria-label="Mark the rest as checked"
                      aria-describedby={markRest.reason ? reasonId : undefined} onClick={() => setAskedFor(blocker)}>
                Mark the rest as checked…
              </button>
            ) : (
              <ConfirmButton
                name="the rest"
                label="Mark the rest as checked…"
                ariaLabel="Mark the rest as checked"
                confirmLabel="Confirm: record untouched lines as approved"
                verb="mark checked"
                tone="primary"
                size="sm"
                firstClass="check-queue-link"
                busy={busy}
                disabled={markRest.disabled}
                onConfirm={run}
              />
            )}
            <HelpTip label="Mark the rest as checked" id={helpId}>{MARK_REVIEWED_HELP}</HelpTip>
          </span>
        )}
      </div>
      {markRest.reason && <p className="muted" id={reasonId} role="status" data-testid="mark-rest-reason">{markRest.reason}</p>}
      {untranslated && (
        <p className="check-queue-untranslated">
          {untranslated}{' '}
          <button type="button" className="check-queue-link" onClick={onUntranslated}>Show the untranslated lines</button>
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
