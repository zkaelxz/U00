import { useState } from 'react'

import { copyTitleTermsToSeries, getTitleTerms } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'
import type { GlossaryTitleTerm } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { copyResultText, titleTermsSummary } from './glossaryTitleTermsModel'

// Shown only when the title has terms of its own besides its series' glossary (it had a
// glossary before joining a series). They stay in use; copying them into the series is a choice.
export function GlossaryTitleTerms({ onCopied }: { onCopied: () => void }) {
  const { dramaId, drama } = useStage()
  const [terms, setTerms] = useState<GlossaryTitleTerm[]>([])
  const [reloads, setReloads] = useState(0)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  useCancellableEffect(
    (cancelled) => {
      getTitleTerms(dramaId).then(
        (t) => !cancelled() && setTerms(t),
        (e: unknown) => !cancelled() && setError(e),
      )
    },
    [dramaId, drama.series_id, reloads],
  )

  if (!terms.length) return answer ? <p className="muted" role="status">{answer}</p> : null
  return (
    <div className="glossary-title-terms" role="group" aria-label="Terms of this title">
      <p className="muted">{titleTermsSummary(terms)}</p>
      <ul className="muted">
        {terms.map((t) => (
          <li key={t.term_original}>{t.term_original} → {t.term_translation}{t.clashes_with_series ? ' (also in the series)' : ''}</li>
        ))}
      </ul>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <button
        type="button"
        className={buttonClass('secondary')}
        disabled={busy}
        title="Adds these terms to the series glossary. Terms the series already has are left as they are; the title keeps all of its own."
        onClick={() => {
          setBusy(true)
          setError(null)
          copyTitleTermsToSeries(dramaId).then(
            (r) => {
              setAnswer(copyResultText(r))
              setBusy(false)
              setReloads((n) => n + 1)
              onCopied()
            },
            (e: unknown) => {
              setError(e)
              setBusy(false)
            },
          )
        }}
      >
        Copy to series
      </button>
    </div>
  )
}
