import { useState } from 'react'

import {
  applyLlmResegmentPreview,
  getLlmResegmentPreview,
  previewResegment,
  startLlmResegmentPreview,
  startResegment,
} from '../../../../api/restructure'
import { ApiError } from '../../../../api/client'
import { getTranslateConfig } from '../../../../api/translateStage'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { TypedConfirm } from '../../../../components/TypedConfirm'
import { humanize } from '../../../../components/labels'
import { Toggle } from '../../../../components/Toggle'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { jobSucceeded, type JobRecord } from '../../../../types/jobs'
import type { ResegmentLlmPreview as LlmPreview, ResegmentPreview } from '../../../../types/restructure'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'
import { EngineModelFields } from './EngineModelFields'
import { ResegmentLlmPreview } from './ResegmentLlmPreview'
import {
  JOB_RUNNING_MESSAGE,
  RESEGMENT_PREVIEW_AGAIN,
  canResegmentWith,
  llmApplyProblem,
  llmApplyProblemText,
  resegmentCostNote,
  resegmentEngines,
  resegmentSummary,
  structureErrorText,
} from './reviewLogic'
import { lineNumber } from '../../../../lineNumber'
import './resegment.css'

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

const SHOWN_CHANGES = 8

// Which of this section's jobs the job panel is following.
type Phase = 'rules' | 'ai-preview' | 'ai-apply'

// Re-segmentation. Rules: a read-only preview first, then a typed "resegment"
// runs it as a job. With AI: a preview job asks the AI where to split (nothing
// is written), then Apply commits exactly that preview, or Discard drops it.
// Either way a snapshot is taken before the lines change.
export function StructureSection({ dramaId, jobRunning, onChanged }: Props) {
  const { onJobDone } = useStage()
  const [useAi, setUseAi] = useState(false)
  const [preview, setPreview] = useState<ResegmentPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // AI path: engine choices, the finished preview, and a plain-text notice
  // after a refused apply (needs confirm, lines changed, preview gone).
  const [config, setConfig] = useState<TranslateRunConfig | null>(null)
  const [pick, setPick] = useState({ engine: '', model: '' })
  const [aiPreview, setAiPreview] = useState<LlmPreview | null>(null)
  const [confirmAsked, setConfirmAsked] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('rules')
  const [jobId, setJobId, runKey] = useJobRun()
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone: (j) => jobDone(j) })
  const running = jobId !== null && !done && !pollError
  const blocked = jobRunning || running ? JOB_RUNNING_MESSAGE : null

  const clearAi = () => {
    setAiPreview(null)
    setConfirmAsked(false)
    setNotice(null)
  }

  const fetchAiPreview = (quiet: boolean) =>
    getLlmResegmentPreview(dramaId).then(
      (p) => {
        setAiPreview((cur) => (quiet && cur ? cur : p))
        setConfirmAsked(false)
        if (!quiet) setNotice(null)
      },
      (e) => {
        if (quiet) return
        if (e instanceof ApiError && e.status === 404) setNotice(RESEGMENT_PREVIEW_AGAIN)
        else setError(e)
      },
    )

  function jobDone(j: JobRecord) {
    if (phase === 'rules') {
      setPreview(null)
      onJobDone()
      onChanged()
    } else if (phase === 'ai-preview') {
      if (jobSucceeded(j)) void fetchAiPreview(false)
    } else if (jobSucceeded(j)) {
      clearAi()
      onJobDone()
      onChanged()
    } else if (j.status === 'error') {
      // A refusal the apply job found at run time: nothing was written.
      const problem = llmApplyProblem(j.error ?? '')
      if (problem === 'confirm') setConfirmAsked(true)
      if (problem === 'changed') setAiPreview(null)
      if (problem) {
        setNotice(llmApplyProblemText(problem))
        setJobId(null)
      }
    }
  }

  const toggleAi = (on: boolean) => {
    setUseAi(on)
    setError(null)
    if (!on) return
    if (!config) getTranslateConfig(dramaId).then(setConfig, () => {})
    // A preview made earlier (it lives on the server until applied) shows again.
    if (!aiPreview && !running) void fetchAiPreview(true)
  }

  const load = () => {
    setLoading(true)
    setError(null)
    previewResegment(dramaId)
      .then(setPreview, setError)
      .finally(() => setLoading(false))
  }
  const start = () => {
    if (!preview) return
    setError(null)
    startResegment(dramaId, { expected_line_ids: preview.source_line_ids, confirm: true }).then((r) => {
      setPhase('rules')
      setJobId(r.job_id)
    }, setError)
  }

  const startAiPreview = () => {
    setError(null)
    clearAi()
    startLlmResegmentPreview(dramaId, pick).then((r) => {
      setPhase('ai-preview')
      setJobId(r.job_id)
    }, setError)
  }
  const applyAi = (confirm: boolean) => {
    if (!aiPreview) return
    setError(null)
    setNotice(null)
    applyLlmResegmentPreview(dramaId, aiPreview.source_line_ids, confirm).then(
      (r) => {
        setPhase('ai-apply')
        setJobId(r.job_id)
      },
      (e) => {
        const problem = llmApplyProblem(e)
        if (!problem) return setError(e)
        if (problem === 'confirm') setConfirmAsked(true)
        if (problem === 'changed' || problem === 'gone') setAiPreview(null)
        setNotice(llmApplyProblemText(problem))
      },
    )
  }
  const discardAi = () => {
    clearAi()
    if (!running) setJobId(null)
  }

  const engines = resegmentEngines(config?.engines ?? [])
  const defaultEngine = config?.translation_engine ?? ''
  const engine = pick.engine || defaultEngine
  const engineOk = canResegmentWith(engine)
  const engineSummary = `${pick.engine ? humanize('engine', pick.engine) : `Default${defaultEngine ? ` (${humanize('engine', defaultEngine)})` : ''}`} · ${pick.model || 'engine default model'}`

  return (
    <div role="group" aria-label="Structure">
      <Section storageKey="review.structure" title="Structure" summary={useAi ? 'Re-segment with AI · restore' : 'Re-segment · restore'}>
        <div className="setting-list review-toggles">
          <Field
            label="Use AI"
            help="Asks an AI engine where to split long lines the rules can't. You see its proposal before anything changes."
          >
            <Toggle checked={useAi} onChange={toggleAi} />
          </Field>
        </div>
        {useAi ? (
          <>
            <Section storageKey="review.structure.ai" title="Advanced" summary={engineSummary}>
              <div className="review-edit-row">
                <EngineModelFields
                  engines={engines}
                  defaultEngine={defaultEngine}
                  engine={pick.engine}
                  model={pick.model}
                  help="Which service suggests split points. The default is the drama's engine; engines marked (no key) cannot run. Translation-only engines can't do this and are not listed."
                  onChange={setPick}
                />
              </div>
            </Section>
            {!engineOk && (
              <p className="error" role="alert">
                {humanize('engine', engine)} only translates and can't suggest split points. Pick another engine.
              </p>
            )}
            <p className="muted" data-testid="resegment-ai-cost">
              {resegmentCostNote(config, engine)}
            </p>
            <div className="actions">
              <button type="button" disabled={running || !engineOk} onClick={startAiPreview}>
                {running && phase === 'ai-preview' ? 'Previewing…' : aiPreview ? 'Preview again with AI' : 'Preview with AI'}
              </button>
            </div>
            {notice && (
              <p className="reseg-ai-warn" role="alert" data-testid="resegment-ai-notice">
                {notice}
              </p>
            )}
            <ErrorBanner error={error} onDismiss={() => setError(null)} />
            {aiPreview && (
              <ResegmentLlmPreview
                preview={aiPreview}
                needsConfirm={aiPreview.needs_confirm || confirmAsked}
                blocked={jobRunning ? JOB_RUNNING_MESSAGE : null}
                busy={running}
                onApply={applyAi}
                onDiscard={discardAi}
              />
            )}
          </>
        ) : (
          <>
            <div className="actions">
              <button type="button" disabled={loading || running} onClick={load}>
                {loading ? 'Checking…' : preview ? 'Preview again' : 'Preview re-segmentation'}
              </button>
            </div>
            {structureErrorText(error) ? (
              <p className="error" role="alert">{structureErrorText(error)}</p>
            ) : (
              <ErrorBanner error={error} onDismiss={() => setError(null)} />
            )}
            {preview && (
              <div className="stack" data-testid="resegment-preview">
                <p>{resegmentSummary(preview)}</p>
                {preview.changed.length === 0 ? (
                  <p className="muted">Nothing to re-segment. Turn on Use AI to split lines the rules can't.</p>
                ) : (
                  <>
                    <ul className="review-matches">
                      {preview.changed.slice(0, SHOWN_CHANGES).map((c) => (
                        <li key={`${c.line_id ?? 'x'}-${c.idx}`}>
                          <span className="muted">#{lineNumber(c.idx)}</span> <span lang="zh">{c.pieces.join(' | ')}</span>
                        </li>
                      ))}
                      {preview.changed.length > SHOWN_CHANGES && (
                        <li className="muted">and {preview.changed.length - SHOWN_CHANGES} more</li>
                      )}
                    </ul>
                    <TypedConfirm word="resegment" action="Re-segment lines" blocked={blocked} busy={running} onConfirm={start}>
                      <p className="muted">
                        Your current lines are saved as a snapshot first (Records → Line history).
                      </p>
                    </TypedConfirm>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </Section>
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
