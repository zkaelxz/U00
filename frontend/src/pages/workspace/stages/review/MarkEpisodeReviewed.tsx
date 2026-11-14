import { useState } from 'react'

import { markEpisodeReviewed } from '../../../../api/review'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { MARK_REVIEWED_HELP, markEpisodeReviewedText } from './markEpisodeReviewedText'

// One explicit action per episode: bulk approval of lines nobody opened teaches
// translation memory and the "approved examples" Translate option, so it is never automatic.
export function MarkEpisodeReviewed({ dramaId, jobRunning, onChanged }: {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState<string | null>(null)

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
    <Section storageKey="review.markReviewed" title="Mark episode reviewed" summary="Record the lines you left untouched as approved">
      <div data-testid="mark-reviewed">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <p className="muted">{MARK_REVIEWED_HELP}</p>
        <ConfirmButton
          name="this episode"
          label="Mark episode reviewed…"
          confirmLabel="Confirm: record untouched lines as approved"
          verb="mark reviewed"
          tone="primary"
          size="sm"
          busy={busy}
          disabled={jobRunning}
          onConfirm={run}
        />
        {jobRunning && <p className="muted">Another job is running on this title. Try again when it finishes.</p>}
        {note && <p className="muted" role="status">{note}</p>}
      </div>
    </Section>
  )
}
