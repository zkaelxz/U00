import { useEffect, useState } from 'react'

import { startReviewJob } from '../../../../api/review'
import { getTranslateConfig } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { humanize } from '../../../../components/labels'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useReattachJob } from '../../../../hooks/useReattachJob'
import { reviewJobIds } from '../../stageJobIds'
import type { ReviewJobBody, ReviewJobKind } from '../../../../types/review'
import type { TranslateEngine } from '../../../../types/translate'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'
import { ReviewFindings } from './ReviewFindings'
import {
  EMPTY_CHECK_FORM,
  EMPTY_FIX_FORM,
  checkFormSummary,
  checkJobBody,
  fixFlaggedBody,
  fixFormSummary,
  spendText,
  type CheckForm,
  type FixForm,
  type GoToLine,
} from './reviewResults'

const KINDS: { kind: ReviewJobKind; label: string }[] = [
  { kind: 'consistency', label: 'Check consistency' },
  { kind: 'emotion', label: 'Tag emotion' },
  { kind: 'notes', label: 'Generate notes' },
  { kind: 'flag', label: 'Flag lines for a second look' },
]

// Engine and model pickers shared by the check jobs and fix-flagged. Paid
// engines stay behind the server's engines.paid check; this only chooses.
function EngineModelFields({
  engines,
  defaultEngine,
  engine,
  model,
  help,
  onChange,
}: {
  engines: TranslateEngine[]
  defaultEngine: string
  engine: string
  model: string
  help: string
  onChange: (next: { engine: string; model: string }) => void
}) {
  const models = engines.find((e) => e.name === (engine || defaultEngine))?.models ?? null
  const engineLabel = (name: string) => {
    const e = engines.find((x) => x.name === name)
    return e ? `${e.label}${e.key_configured ? '' : ' (no key)'}` : humanize('engine', name)
  }
  return (
    <>
      <Field label="Engine" help={help}>
        <select value={engine} onChange={(e) => onChange({ engine: e.target.value, model: '' })}>
          <option value="">Default{defaultEngine ? ` (${humanize('engine', defaultEngine)})` : ''}</option>
          {engines.map((e) => (
            <option key={e.name} value={e.name}>
              {engineLabel(e.name)}
            </option>
          ))}
        </select>
      </Field>
      {models && models.length > 0 ? (
        <Field label="Model">
          <select value={model} onChange={(e) => onChange({ engine, model: e.target.value })}>
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
          <input value={model} maxLength={200} onChange={(e) => onChange({ engine, model: e.target.value })} />
        </Field>
      )}
    </>
  )
}

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  onGoTo: GoToLine
  // Flagged lines in the drama (from the editor); null until known.
  flaggedCount: number | null
}

// After every job reaches a terminal state (done, error or cancelled) the
// stage's lines, records and stored results are refetched, so translated
// text, flags and findings never stay stale until a hard refresh.
export function ReviewJobsPanel({ dramaId, reloads, onChanged, onGoTo, flaggedCount }: Props) {
  const { onJobDone, drama } = useStage()
  const [jobId, setJobId, runKey] = useJobRun()
  useReattachJob(reviewJobIds(dramaId), jobId, setJobId)
  const [error, setError] = useState<unknown>(null)
  const [fix, setFix] = useState<FixForm>(EMPTY_FIX_FORM)
  const [checks, setChecks] = useState<CheckForm>(EMPTY_CHECK_FORM)
  const [problem, setProblem] = useState<string | null>(null)
  // Bumped when a job finishes; only jobs change the stored findings.
  const [jobsDone, setJobsDone] = useState(0)
  // Engine and model choices for every AI job; without them only the defaults are offered.
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
  const cuesOn = checks.audioCues ?? drama.has_audio
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
            <button key={kind} type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void start(kind, checkJobBody(kind, checks))}>
              {label}
            </button>
          ))}
        </div>
        <Section storageKey="review.ai.options" title="Check options" summary={checkFormSummary(checks, defaultEngine, drama.has_audio)}>
          <div className="review-edit-row">
            <EngineModelFields
              engines={engines}
              defaultEngine={defaultEngine}
              engine={checks.engine}
              model={checks.model}
              help="Which service runs these checks. The default is the drama's engine; engines marked (no key) cannot run, and translation-only engines cannot run these checks."
              onChange={(n) => setChecks((c) => ({ ...c, ...n }))}
            />
            <div className="setting-list review-toggles">
              <Field label="Tag emotion: use audio delivery cues">
                <Toggle checked={cuesOn} onChange={(on) => setChecks((c) => ({ ...c, audioCues: on }))} />
              </Field>
            </div>
          </div>
        </Section>
        {busy && <p className="muted review-ai-busy">A review job is running.</p>}
        <fieldset className="review-fix" aria-label="Fix flagged lines">
          <legend>Fix flagged lines</legend>
          <p className="muted review-fix-hint">Redoes the source and English of every flagged line.</p>
          <div className="review-actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy || noFlagged} onClick={startFix}>
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
              <EngineModelFields
                engines={engines}
                defaultEngine={defaultEngine}
                engine={fix.engine}
                model={fix.model}
                help="Which service re-translates. The default comes from Settings; engines marked (no key) cannot run."
                onChange={(n) => setFix((f) => ({ ...f, ...n }))}
              />
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
      <ReviewFindings dramaId={dramaId} jobsDone={jobsDone} reloads={reloads} onGoTo={onGoTo} />
    </div>
  )
}
