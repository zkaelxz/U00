import { useState } from 'react'

import { applyEnCleanup, previewEnCleanup } from '../../../../api/reviewExtras'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { EnCleanupPreview } from '../../../../types/reviewExtras'
import { cleanupDone, cleanupSummary } from './aiExtrasLogic'
import { JOB_RUNNING_MESSAGE } from './reviewLogic'

const SHOWN = 8

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

// Fix common errors in the English (spacing, punctuation, quotes, "i", doubled
// words, stray CJK punctuation) with fixed rules, no AI. Preview first; apply
// re-checks the plan on the server (409 if the lines moved) and saves a Line
// history snapshot, which is the undo.
export function AiExtrasCleanup({ dramaId, jobRunning, onChanged }: Props) {
  const [preview, setPreview] = useState<EnCleanupPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = () => {
    setBusy(true)
    setError(null)
    setDone(null)
    previewEnCleanup(dramaId)
      .then(setPreview, setError)
      .finally(() => setBusy(false))
  }

  const apply = () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    applyEnCleanup(dramaId, preview.plan_hash)
      .then((r) => {
        setPreview(null)
        setDone(cleanupDone(r))
        onChanged()
      }, setError)
      .finally(() => setBusy(false))
  }

  return (
    <Section storageKey="review.aiExtras.cleanup" title="Fix common errors" summary="Spacing, punctuation, quotes · no AI">
      <p className="muted">
        Tidies the English with fixed rules: double spaces, spacing around punctuation, ellipses and quotes (following what the title mostly uses), a
        lowercase "i", doubled words and stray full-width punctuation. Speaker labels, glossary terms, tags and non-English lines are left alone.
      </p>
      <div className="actions">
        <button type="button" className={buttonClass(preview ? 'secondary' : 'primary')} disabled={busy} onClick={load}>
          {busy && !preview ? 'Checking…' : preview ? 'Preview again' : 'Preview fixes'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {done && (
        <p role="status" className="muted">
          {done}
        </p>
      )}
      {preview && (
        <div className="stack" data-testid="en-cleanup-preview">
          <p>{cleanupSummary(preview)}</p>
          {preview.changes.length > 0 && (
            <>
              <ul className="review-matches">
                {preview.rules.map((r) => (
                  <li key={r.rule}>
                    {r.label} <span className="muted">· {r.lines} line{r.lines === 1 ? '' : 's'}</span>
                  </li>
                ))}
              </ul>
              <ul className="review-matches">
                {preview.changes.slice(0, SHOWN).map((c) => (
                  <li key={c.line_id}>
                    <span className="muted">#{lineNumber(c.idx)}</span> <del>{c.before}</del> → <ins>{c.after}</ins>
                  </li>
                ))}
                {preview.lines_changed > Math.min(SHOWN, preview.changes.length) && (
                  <li className="muted">and {preview.lines_changed - Math.min(SHOWN, preview.changes.length)} more</li>
                )}
              </ul>
              <div className="actions">
                <button type="button" className={buttonClass('primary')} disabled={busy || jobRunning} onClick={apply}>
                  {busy ? 'Fixing…' : `Fix ${preview.lines_changed} line${preview.lines_changed === 1 ? '' : 's'}`}
                </button>
              </div>
              {jobRunning && <p className="muted">{JOB_RUNNING_MESSAGE}</p>}
              <p className="muted">Your current lines are saved as a snapshot first. To undo, restore it from Records → Line history.</p>
            </>
          )}
        </div>
      )}
    </Section>
  )
}
