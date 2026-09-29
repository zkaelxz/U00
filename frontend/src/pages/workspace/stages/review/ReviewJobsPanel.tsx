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
import { EMPTY_FIX_FORM, fixFlaggedBody, fixFormSummary, spendText, type FixForm, type GoToLine } from './reviewResults'

const KINDS: { kind: ReviewJobKind; label: string }[] = [
  { kind: 'consistency', label: 'Check consistency' },
  { kind: 'emotion', label: 'Tag emotion' },
  { kind: 'notes', label: 'Generate notes' },
  { kind: 'flag', label: 'Flag lines for a second look' },
]

interface Props {
  dramaId: number
  onChanged: () => void
  onGoTo: GoToLine
  // Flagged lines in the drama (from the editor); null until known.
  flaggedCount: number | null
}

// After every job reaches a terminal state (done, error or cancelled) the
// stage's lines, records and stored results are refetched, so translated
// text, flags and findings never stay stale until a hard refresh.
export function ReviewJobsPanel({ dramaId, onChanged, onGoTo, flaggedCount }: Props) {
  const { onJobDone } = useStage()
  const [jobId, setJobId, runKey] = useJobRun()
  const [error, setError] = useState<unknown>(null)
  const [fix, setFix] = useState<FixForm>(EMPTY_FIX_FORM)
  const [problem, setProblem] = useState<string | null>(null)
  // Bumped when a job finishes; only jobs change the stored findings.
  const [jobsDone, setJobsDone] = useState(0)
  // Engine and model choices for fix-flagged; without them only the defaults are offered.
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      setJobsDone((n) => n + 1)
      onJobDone()
      onChanged()
    },
  })
  const busy = jobId !== null && !done && !pollError
  const noFlagged = flaggedCount === 0

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
  const capHelp =
    'Stops the fix at this many dollars; blank means no per-job cap.' +
    (config ? ` ${spendText(config.month_spend, config.monthly_cap_usd)}` : '')

  // The job status and start errors sit above the folded section, so a
  // running or reattached job is always visible.
  return (
    <div aria-label="AI checks" role="group" className="review-ai">
      {jobId && <JobPanel job={job} pollError={pollError} />}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Section storageKey="review.ai" title="AI review" summary="consistency, emotion, notes, flag, fix flagged">
        <div className="review-actions review-ai-actions">
          {KINDS.map(({ kind, label }) => (
            <button key={kind} type="button" disabled={busy} onClick={() => void start(kind)}>
              {label}
            </button>
          ))}
        </div>
        {busy && <p className="muted review-ai-busy">A review job is running.</p>}
        <fieldset className="review-fix" aria-label="Fix flagged lines">
          <legend>Fix flagged lines</legend>
          <p className="muted review-fix-hint">Redoes the source and English of every flagged line.</p>
          <div className="review-actions">
            <button type="button" disabled={busy || noFlagged} onClick={startFix}>
              Fix flagged lines
            </button>
            {noFlagged && <span className="muted">Still needed: a flagged line.</span>}
          </div>
          {problem && (
            <p className="error" role="alert">
              {problem}
            </p>
          )}
          <Section storageKey="review.fix.options" title="Options" summary={fixFormSummary(fix, defaultEngine)}>
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
              {/* Text, not type=number: a browser turns a value it cannot parse ("5$", "1,5", "-")
                  into "", which would start a paid job with no cap. fixFlaggedBody checks every value. */}
              <Field label="Cost cap" unit="$" help={capHelp}>
                <input
                  inputMode="decimal"
                  autoComplete="off"
                  value={fix.cap}
                  onChange={(e) => {
                    setProblem(null)
                    setFix((f) => ({ ...f, cap: e.target.value }))
                  }}
                />
              </Field>
            </div>
          </Section>
        </fieldset>
      </Section>
      <ReviewFindings dramaId={dramaId} jobsDone={jobsDone} onGoTo={onGoTo} />
    </div>
  )
}
