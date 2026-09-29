import { useState } from 'react'

import { previewResegment, startResegment } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { TypedConfirm } from '../../../../components/TypedConfirm'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import type { ResegmentPreview } from '../../../../types/restructure'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'
import { JOB_RUNNING_MESSAGE, resegmentSummary, structureErrorText } from './reviewLogic'

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

const SHOWN_CHANGES = 8

// Re-segmentation: a read-only preview first, then a typed "resegment" to run
// it as a job (it re-splits lines and saves; a snapshot is taken first).
export function StructureSection({ dramaId, jobRunning, onChanged }: Props) {
  const { onJobDone } = useStage()
  const [preview, setPreview] = useState<ResegmentPreview | null>(null)
  const [useAi, setUseAi] = useState(false)
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      setPreview(null)
      onJobDone()
      onChanged()
    },
  })
  const running = jobId !== null && !done && !pollError
  const blocked = jobRunning || running ? JOB_RUNNING_MESSAGE : null

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
    startResegment(dramaId, {
      expected_line_ids: preview.source_line_ids,
      confirm: true,
      use_llm: useAi,
      engine: engine.trim() || null,
      model: model.trim() || null,
    }).then((r) => setJobId(r.job_id), setError)
  }

  return (
    <div role="group" aria-label="Structure">
      <Section storageKey="review.structure" title="Structure" summary="Re-segment · restore">
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
              <p className="muted">Nothing to re-segment.</p>
            ) : (
              <>
                <ul className="review-matches">
                  {preview.changed.slice(0, SHOWN_CHANGES).map((c) => (
                    <li key={`${c.line_id ?? 'x'}-${c.idx}`}>
                      <span className="muted">#{c.idx}</span> <span lang="zh">{c.pieces.join(' | ')}</span>
                    </li>
                  ))}
                  {preview.changed.length > SHOWN_CHANGES && (
                    <li className="muted">and {preview.changed.length - SHOWN_CHANGES} more</li>
                  )}
                </ul>
                <label className="review-check">
                  <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Use AI
                </label>
                {useAi && (
                  <Section title="Advanced" summary={`engine ${engine || 'default'} · model ${model || 'default'}`}>
                    <div className="review-edit-row">
                      <Field label="Engine" help="Blank uses the default translation engine from Settings.">
                        <input value={engine} onChange={(e) => setEngine(e.target.value)} />
                      </Field>
                      <Field label="Model" help="Blank uses the engine's default model.">
                        <input value={model} onChange={(e) => setModel(e.target.value)} />
                      </Field>
                    </div>
                  </Section>
                )}
                <TypedConfirm word="resegment" action="Re-segment lines" blocked={blocked} busy={running} onConfirm={start}>
                  <p className="muted">
                    Your current lines are saved as a snapshot first (Records → Line history).
                  </p>
                </TypedConfirm>
              </>
            )}
          </div>
        )}
      </Section>
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
