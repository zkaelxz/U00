import { useState } from 'react'

import { dismissTranslateErrors, redoDoubtfulLines, redoGlossaryFlagged, retryFailedLines } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { humanize } from '../../../components/labels'
import type { TranslateRunConfig } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { failedBatches, type RunForm } from '../translateForm'
import { buildRetryBody, failedLinesText, retryBlocker, retryLabel, splitFailed } from './followupModel'

interface RunProps {
  config: TranslateRunConfig
  // The Translate form as it stands; null until it has loaded.
  form: RunForm | null
  busy: boolean
  onStarted: (jobId: string) => void
}

// Warning about the last run's failed batches. Dismiss clears only that record.
// Retry translates exactly the failed lines (forced for them only), so lines
// that kept an earlier engine's English can be retried too.
export function FailedBatchesNotice({ config, form, busy, onStarted, onDismissed }:
  RunProps & { onDismissed: () => void }) {
  const { dramaId } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const failed = failedBatches(config.last_translate_errors)
  if (!failed) return null
  const { empty, kept } = splitFailed(failed.lineNumbers, config.failed_untranslated_numbers, config.failed_kept_earlier_numbers)
  const total = empty.length + kept.length
  const canRetry = config.failed_retryable === true
  const blocker = form ? retryBlocker(form, config.translation_engine) : null
  const act = (call: Promise<unknown>, done: () => void) => {
    setPending(true)
    setError(null)
    call.then(done, (e: unknown) => setError(e)).finally(() => setPending(false))
  }
  const dismiss = () => act(dismissTranslateErrors(dramaId), onDismissed)
  const retry = () => {
    if (!form) return
    act(retryFailedLines(dramaId, buildRetryBody(form)).then((r) => onStarted(r.job_id)), () => undefined)
  }
  return (
    <section className="panel translate-failed" aria-label="Failed batches" data-testid="failed-batches">
      <p className="warn">
        The last translation run had {failed.batches} failed batch{failed.batches === 1 ? '' : 'es'}
        {total > 0 && <>; {failedLinesText(empty, kept)}</>}.
        {canRetry ? null : ' This record is from an older version, so the lines cannot be retried from here.'}
        {canRetry && empty.length > 0 && kept.length === 0 && ' Translate again to retry only the missing lines; lines already translated are skipped.'}
        {canRetry && kept.length > 0 && ' Retrying replaces the earlier English on those lines with the engine chosen below.'}
      </p>
      {failed.reasons.length > 0 && (
        <ul className="muted translate-failed-reasons">
          {failed.reasons.slice(0, 3).map((r) => <li key={r}>{r}</li>)}
          {failed.reasons.length > 3 && <li>and {failed.reasons.length - 3} more</li>}
        </ul>
      )}
      {canRetry && blocker && <p className="error" role="alert">{blocker}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="actions">
        {canRetry && total > 0 && (
          <button type="button" onClick={retry} disabled={pending || busy || !form || blocker !== null}>
            {retryLabel(total)}
          </button>
        )}
        <button type="button" onClick={dismiss} disabled={pending}>Dismiss notice</button>
      </div>
    </section>
  )
}

// Lines the glossary check could not fix on the run's own engine, with a
// one-click redo on the stronger engine from Settings. The server applies the
// monthly cap and writes the usage log, as for any run.
export function GlossaryFlaggedNotice({ config, busy, onStarted }: Omit<RunProps, 'form'>) {
  const { dramaId } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const count = config.glossary_flagged_count ?? 0
  if (count === 0) return null
  const engine = config.glossary_redo_engine
  const redo = () => {
    setPending(true)
    setError(null)
    redoGlossaryFlagged(dramaId).then((r) => onStarted(r.job_id), (e: unknown) => setError(e)).finally(() => setPending(false))
  }
  return (
    <section className="panel translate-failed" aria-label="Glossary check" data-testid="glossary-flagged">
      <p className="warn">
        {count} line{count === 1 ? '' : 's'} still {count === 1 ? 'misses' : 'miss'} a glossary name after a second try
        {' '}and {count === 1 ? 'is' : 'are'} flagged in Review.
        {!engine && ' Pick a stronger engine in Settings to redo them in one click.'}
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {engine && (
        <button type="button" onClick={redo} disabled={pending || busy}>
          Redo these with {humanize('engine', engine)}
        </button>
      )}
    </section>
  )
}

// Lines Review flagged as unlike their source or as uncertain, with the cost shown
// before the one-click redo on the stronger engine from Settings. The server applies
// the monthly cap, writes the usage log and keeps the undo snapshot, as for any run.
export function DoubtfulLinesNotice({ config, busy, onStarted }: Omit<RunProps, 'form'>) {
  const { dramaId } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const count = config.doubtful_flagged_count ?? 0
  if (count === 0) return null
  const engine = config.glossary_redo_engine
  const estimate = config.doubtful_redo_estimate_usd
  const lowerBound = config.doubtful_redo_estimate_is_lower_bound ?? false
  const refused = config.doubtful_redo_monthly_refusal ?? false
  const aboveCap = config.doubtful_redo_above_cap ?? false
  const redo = () => {
    setPending(true)
    setError(null)
    redoDoubtfulLines(dramaId).then((r) => onStarted(r.job_id), (e: unknown) => setError(e)).finally(() => setPending(false))
  }
  return (
    <section className="panel translate-failed" aria-label="Doubtful lines" data-testid="doubtful-lines">
      <p className="warn">
        {count} line{count === 1 ? ' is' : 's are'} flagged in Review as unlike {count === 1 ? 'its' : 'their'} source or as uncertain.
        {!engine && ' Pick a stronger engine in Settings to redo them in one click.'}
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {engine && (
        <>
          <button type="button" onClick={redo} disabled={pending || busy || refused}>
            Redo {count === 1 ? 'this line' : 'these lines'} with {humanize('engine', engine)}
            {estimate != null && !refused ? `: ${lowerBound ? 'at least' : 'about'} $${estimate.toFixed(2)}` : ''}
          </button>
          {refused && (
            <p className="error" role="alert">This month’s spending cap is used up, so the redo can’t run.</p>
          )}
          {aboveCap && !refused && (
            <p className="error" role="alert">The estimate is above the cap, so the redo would stop early.</p>
          )}
          <p className="muted">
            {lowerBound && 'Thinking is on for this title, so hidden reasoning adds to the cost. '}
            The old English is kept in History, and the monthly cap applies.
          </p>
        </>
      )}
    </section>
  )
}
