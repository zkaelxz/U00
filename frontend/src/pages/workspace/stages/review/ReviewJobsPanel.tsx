import { useEffect, useState } from 'react'

import { startReviewJob } from '../../../../api/review'
import { getTranslateConfig } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import type { ReviewJobBody, ReviewJobKind } from '../../../../types/review'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'
import { ReviewFindings } from './ReviewFindings'
import { EMPTY_FIX_FORM, fixFlaggedBody, fixFormSummary, type FixForm, type GoToLine } from './reviewResults'

const KINDS: { kind: ReviewJobKind; label: string }[] = [
  { kind: 'consistency', label: 'Check consistency' },
  { kind: 'emotion', label: 'Tag emotion' },
  { kind: 'notes', label: 'Generate notes' },
  { kind: 'flag', label: 'Flag lines for a second look' },
]

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  onGoTo: GoToLine
}

// After every job reaches a terminal state (done, error or cancelled) the
// stage's lines, records and stored results are refetched, so translated
// text, flags and findings never stay stale until a hard refresh.
export function ReviewJobsPanel({ dramaId, reloads, onChanged, onGoTo }: Props) {
  const { onJobDone } = useStage()
  const [jobId, setJobId, runKey] = useJobRun()
  const [error, setError] = useState<unknown>(null)
  const [fix, setFix] = useState<FixForm>(EMPTY_FIX_FORM)
  const [problem, setProblem] = useState<string | null>(null)
  // Engine and model choices for fix-flagged; without them only the defaults are offered.
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      onJobDone()
      onChanged()
    },
  })
  const busy = jobId !== null && !done && !pollError

  useEffect(() => {
    let cancelled = false
    getTranslateConfig(dramaId).then((c) => !cancelled && setConfig(c), () => {})
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const start = (kind: ReviewJobKind, body?: ReviewJobBody) =>
    startReviewJob(dramaId, kind, body).then(
      (r) => {
        setError(null)
        setJobId(r.job_id)
      },
      setError,
    )

  const startFix = () => {
    const body = fixFlaggedBody(fix)
    if (typeof body === 'string') {
      setProblem(body)
      return
    }
    setProblem(null)
    void start('fix-flagged', body)
  }

  const defaultEngine = config?.translation_engine ?? ''
  const engines = config?.engines ?? []
  const models = engines.find((e) => e.name === (fix.engine || defaultEngine))?.models ?? null
  const engineLabel = (name: string) => {
    const e = engines.find((x) => x.name === name)
    return e ? `${e.label}${e.key_configured ? '' : ' (no key)'}` : name
  }
  const capHelp = config
    ? `Stop the fix at this many dollars; blank means no cap. Spend this month: $${config.month_spend.toFixed(2)} of $${config.monthly_cap_usd.toFixed(2)}.`
    : 'Stop the fix at this many dollars; blank means no cap.'

  // The job status stays outside the collapsed section so a running or
  // reattached job is always visible.
  return (
    <div aria-label="AI checks" role="group" className="review-ai">
      <Section
        storageKey="review.ai"
        title="AI review"
        count={KINDS.length + 1}
        summary="consistency, emotion, notes, flag, fix flagged"
      >
        <div className="review-actions">
          {KINDS.map(({ kind, label }) => (
            <button key={kind} type="button" disabled={busy} onClick={() => void start(kind)}>
              {label}
            </button>
          ))}
        </div>
        <fieldset className="review-fix" aria-label="Fix flagged lines">
          <legend>Fix flagged lines</legend>
          <p className="muted review-fix-hint">Re-transcribes and re-translates every flagged line. {fixFormSummary(fix, defaultEngine)}</p>
          <div className="review-edit-row">
            <Field label="Engine" help="Which service re-translates. The default comes from Settings; engines marked (no key) cannot run.">
              <select value={fix.engine} onChange={(e) => setFix((f) => ({ ...f, engine: e.target.value, model: '' }))}>
                <option value="">Default{defaultEngine ? ` (${defaultEngine})` : ''}</option>
                {engines.map((e) => (
                  <option key={e.name} value={e.name}>
                    {engineLabel(e.name)}
                  </option>
                ))}
              </select>
            </Field>
            {models && models.length > 0 ? (
              <Field label="Model">
                <select value={fix.model} onChange={(e) => setFix((f) => ({ ...f, model: e.target.value }))}>
                  <option value="">Engine default</option>
                  {models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label="Model" help="Blank uses the engine's default model.">
                <input value={fix.model} maxLength={200} onChange={(e) => setFix((f) => ({ ...f, model: e.target.value }))} />
              </Field>
            )}
            <Field label="Cost cap" unit="$" help={capHelp} error={problem}>
              <input
                type="number"
                inputMode="decimal"
                min={0}
                step="0.01"
                value={fix.cap}
                onChange={(e) => setFix((f) => ({ ...f, cap: e.target.value }))}
              />
            </Field>
          </div>
          <div className="review-actions">
            <button type="button" disabled={busy} onClick={startFix}>
              Fix flagged lines
            </button>
          </div>
        </fieldset>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </Section>
      {jobId && <JobPanel job={job} pollError={pollError} />}
      <ReviewFindings dramaId={dramaId} reloads={reloads} onGoTo={onGoTo} />
    </div>
  )
}
