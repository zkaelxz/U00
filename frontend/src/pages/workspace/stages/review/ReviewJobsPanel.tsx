import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { startReviewJob } from '../../../../api/review'
import { getTranslateConfig } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useReattachJob } from '../../../../hooks/useReattachJob'
import { reviewJobIds } from '../../stageJobIds'
import type { ReviewJobBody, ReviewJobKind } from '../../../../types/review'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { useStage } from '../../StageContext'
import { BulkBatchesPanel } from '../BulkBatchesPanel'
import { JobPanel } from '../JobPanel'
import { EngineModelFields } from './EngineModelFields'
import { ReviewFindings } from './ReviewFindings'
import {
  BULK_HELP,
  bulkBlocker,
  bulkStartedText,
  effectiveEngine,
  reviewBulkAvailable,
  reviewStartBody,
  type BulkChoice,
  type BulkKind,
} from './reviewBulk'
import {
  EMPTY_CHECK_FORM,
  EMPTY_FIX_FORM,
  checkFormSummary,
  fixFlaggedBody,
  fixFormSummary,
  spendText,
  type CheckForm,
  type FixForm,
  type GoToLine,
} from './reviewResults'
import { NO_KEY_ENGINES_HELP } from '../../../../helpText'

const KINDS: { kind: BulkKind; label: string; what: string }[] = [
  { kind: 'consistency', label: 'Check consistency', what: 'Consistency check' },
  { kind: 'emotion', label: 'Tag emotion', what: 'Emotion tags' },
  { kind: 'notes', label: 'Generate notes', what: 'Translator notes' },
  { kind: 'flag', label: 'Flag lines for a second look', what: 'Second-look flags' },
]

// A bulk job waits on the provider for up to 24 hours; its status changes
// rarely (the server checks the batch every minute), so poll it slowly.
const BULK_POLL_MS = 10_000

interface BulkRunInfo {
  kind: BulkKind
  jobId: string
  runKey: number
  label: string
  text: string
}

// One submitted bulk batch's background job (bulk_<kind>_<drama>): its own
// JobPanel and notice, so it never locks the other jobs' buttons while it
// waits. onSubmitted runs when the job's message changes (the batch now
// exists), so the batch list can be re-read.
function BulkRun({ run, onDone, onSubmitted }: { run: BulkRunInfo; onDone: () => void; onSubmitted: () => void }) {
  const { job, done, error } = useJob(run.jobId, { runKey: run.runKey, intervalMs: BULK_POLL_MS, onDone })
  const message = job?.message ?? ''
  useEffect(() => {
    if (message) onSubmitted()
  }, [message, onSubmitted])
  return (
    <div role="group" aria-label={`Bulk: ${run.label}`} className="review-ai" data-testid="bulk-run">
      <JobPanel job={job} pollError={error} />
      {!done && !error && (
        <p className="muted" role="status">
          {run.text}
        </p>
      )}
    </div>
  )
}

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  onGoTo: GoToLine
  // Flagged lines in the drama (from the editor); null until known.
  flaggedCount: number | null
  // More AI-review tools, shown at the end of the fold.
  children?: ReactNode
}

// After every job reaches a terminal state (done, error or cancelled) the
// stage's lines, records and stored results are refetched, so translated
// text, flags and findings never stay stale until a hard refresh.
export function ReviewJobsPanel({ dramaId, reloads, onChanged, onGoTo, flaggedCount, children }: Props) {
  const { onJobDone, drama } = useStage()
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(reviewJobIds(dramaId), adoptJob)
  const [error, setError] = useState<unknown>(null)
  const [fix, setFix] = useState<FixForm>(EMPTY_FIX_FORM)
  const [checks, setChecks] = useState<CheckForm>(EMPTY_CHECK_FORM)
  const [problem, setProblem] = useState<string | null>(null)
  // Bumped when a job finishes; only jobs change the stored findings.
  const [jobsDone, setJobsDone] = useState(0)
  // Engine and model choices for every AI job; without them only the defaults are offered.
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  // The config read failed: the Bulk switches say why they are off.
  const [configFailed, setConfigFailed] = useState(false)
  // R49: Bulk per check (not remembered: a slow run should be a fresh choice).
  const [bulkOn, setBulkOn] = useState<BulkChoice>({})
  const [bulkRuns, setBulkRuns] = useState<BulkRunInfo[]>([])
  const [batchReload, setBatchReload] = useState(0)
  const runSeq = useRef(0)
  const reloadBatches = useCallback(() => setBatchReload((n) => n + 1), [])
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
    getTranslateConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        setConfigFailed(false)
      },
      () => !cancelled && setConfigFailed(true),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const start = (kind: ReviewJobKind, body?: ReviewJobBody, label = '') =>
    startReviewJob(dramaId, kind, body).then(
      (r) => {
        setError(null)
        if (!r.bulk) {
          setJobId(r.job_id)
          return
        }
        runSeq.current += 1
        const run: BulkRunInfo = {
          kind: kind as BulkKind,
          jobId: r.job_id,
          runKey: runSeq.current,
          label,
          text: bulkStartedText(label, r.line_count),
        }
        setBulkRuns((cur) => [...cur.filter((x) => x.kind !== run.kind), run])
        reloadBatches()
      },
      setError,
    )

  // A bulk batch applied: results, lines and the batch list are all stale.
  const bulkDone = () => {
    setJobsDone((n) => n + 1)
    onJobDone()
    onChanged()
    reloadBatches()
  }

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
  const bulkEngines = config?.bulk_supported_engines ?? []
  const engineNow = effectiveEngine(checks, defaultEngine)
  const bulkOk = config !== null && reviewBulkAvailable(engineNow, bulkEngines)
  const bulkReason = config
    ? bulkBlocker(engineNow, bulkEngines)
    : configFailed
      ? "Bulk is off: couldn't load the engine settings. Reload to try again."
      : null
  const anyBulk = bulkOk && KINDS.some(({ kind }) => bulkOn[kind])
  const capHelp =
    'Stops the fix at this many dollars; blank means no per-job cap.' +
    (config ? ` ${spendText(config.month_spend, config.monthly_cap_usd)}` : '')

  // The job status and start errors sit above the folded section, so a
  // running or reattached job is always visible.
  return (
    <div aria-label="AI checks" role="group" className="review-ai">
      {jobId && <JobPanel job={job} pollError={pollError} />}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {bulkRuns.map((r) => (
        <BulkRun key={r.kind} run={r} onDone={bulkDone} onSubmitted={reloadBatches} />
      ))}
      <Section storageKey="review.aiFold" title="AI review" defaultOpen summary="Checks · flag lines · coverage and pacing">
        {/* One row per check: its Start button, then its Bulk switch. */}
        <div role="list" aria-label="AI checks to run" className="stack">
          {KINDS.map(({ kind, label, what }) => (
            <div key={kind} role="listitem" className="review-job-row" data-testid={`review-job-${kind}`}>
              <button
                type="button"
                className={buttonClass('secondary')}
                disabled={busy}
                onClick={() => void start(kind, reviewStartBody(kind, checks, bulkOn, defaultEngine, bulkEngines), what)}
              >
                {label}
              </button>
              <div className="setting-list review-toggles">
                <Field label="Bulk" help={BULK_HELP}>
                  <Toggle
                    aria-label={`Bulk: ${label}`}
                    checked={bulkOk && !!bulkOn[kind]}
                    disabled={!bulkOk}
                    onChange={(on) => setBulkOn((c) => ({ ...c, [kind]: on }))}
                  />
                </Field>
              </div>
            </div>
          ))}
        </div>
        {bulkReason && <p className="muted">{bulkReason}{engineNow === 'gemini' && <> <a href="#/settings">Open Settings</a></>}</p>}
        {anyBulk && (
          <p className="muted" data-testid="bulk-warning">
            Bulk is half price but slow: results can take up to 24 hours. Until they arrive, structural edits (add, delete, merge, split,
            re-segment, restore a version, delete the drama) are refused.
          </p>
        )}
        <Section storageKey="review.ai.options" title="Check options" summary={checkFormSummary(checks, defaultEngine, drama.has_audio)}>
          <div className="review-edit-row">
            <EngineModelFields
              engines={engines}
              defaultEngine={defaultEngine}
              engine={checks.engine}
              model={checks.model}
              help={`Which service runs these checks. The default is the drama's engine. ${NO_KEY_ENGINES_HELP} Translation-only engines cannot run these checks.`}
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
                help={`Which service re-translates. The default comes from Settings. ${NO_KEY_ENGINES_HELP}`}
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
        <BulkBatchesPanel reloadKey={batchReload} />
        <ReviewFindings dramaId={dramaId} jobsDone={jobsDone} reloads={reloads} onGoTo={onGoTo} />
        {children}
      </Section>
    </div>
  )
}
