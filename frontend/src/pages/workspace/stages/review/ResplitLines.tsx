import { useCallback, useEffect, useState } from 'react'

import { getDiarizationConfig } from '../../../../api/workspace'
import { ApiError } from '../../../../api/client'
import { listAllLines, reassignSpeakersFromSaved, resplitLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { Toggle } from '../../../../components/Toggle'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useReattachJob } from '../../../../hooks/useReattachJob'
import { jobSucceeded, type JobRecord } from '../../../../types/jobs'
import type { ResplitResult } from '../../../../types/restructure'
import { useStage } from '../../StageContext'
import { resplitJobId } from '../../stageJobIds'
import { JobPanel } from '../JobPanel'
import type { SpeakerTimeSummary } from '../../../../types/workspace'
import {
  JOB_RUNNING_MESSAGE,
  resplitNeedsConfirm,
  resplitSummary,
  speakerTimeFooter,
  speakerTimeLines,
  structureErrorText,
} from './reviewLogic'

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

// Cuts over-long lines in place from the text already saved, and relabels
// only the split lines from the detection already saved: nothing is
// transcribed or detected again. A snapshot is taken first (Records -> Line history).
export function ResplitLines({ dramaId, jobRunning, onChanged }: Props) {
  const { onJobDone } = useStage()
  const [align, setAlign] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [needsConfirm, setNeedsConfirm] = useState(false)
  const [summary, setSummary] = useState<string | null>(null)
  const [speakers, setSpeakers] = useState<SpeakerTimeSummary | null>(null)
  const loadSpeakers = useCallback(
    () => getDiarizationConfig(dramaId).then((c) => setSpeakers(c.speaker_summary ?? null), () => {}),
    [dramaId],
  )
  useEffect(() => {
    void loadSpeakers()
  }, [loadSpeakers])
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob([resplitJobId(dramaId)], adoptJob)
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone: (j) => jobDone(j) })
  const running = jobId !== null && !done && !pollError
  const blocked = jobRunning || running || busy
  const wait = (jobRunning || running) ? JOB_RUNNING_MESSAGE : null

  function jobDone(j: JobRecord) {
    const r = (j.result ?? {}) as ResplitResult & { failed_reason?: string; detail?: string }
    if (jobSucceeded(j) && !r.failed_reason) setSummary(resplitSummary(r))
    else if (r.failed_reason === 'not_applied') setSummary(`Nothing was changed. ${r.detail ?? ''}`.trim())
    onJobDone()
    onChanged()
    void loadSpeakers()
  }

  const run = async (confirm: boolean) => {
    setBusy(true)
    setError(null)
    setSummary(null)
    setNeedsConfirm(false)
    try {
      const lines = await listAllLines(dramaId)
      const r = await resplitLines(dramaId, {
        expected_line_ids: lines.map((l) => l.id), align_to_audio: align, confirm,
      })
      if (r.job_id) setJobId(r.job_id)
      else {
        setSummary(resplitSummary(r))
        if (r.split_lines) onChanged()
        void loadSpeakers()
      }
    } catch (e) {
      if (resplitNeedsConfirm(e)) setNeedsConfirm(true)
      else setError(e)
    } finally {
      setBusy(false)
    }
  }

  const reassign = async () => {
    setBusy(true)
    setError(null)
    setSummary(null)
    try {
      const r = await reassignSpeakersFromSaved(dramaId)
      setSummary(`Speakers re-assigned from the saved detection: ${r.changed} changed${r.kept_manual ? `, ${r.kept_manual} kept as you set them` : ''}.`)
      if (r.changed) onChanged()
      void loadSpeakers()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && !/job/i.test(e.message)) setSummary(e.message)
      else setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div role="group" aria-label="Re-split long lines">
      <Section storageKey="review.resplit" title="Re-split long lines" summary="Cut long blocks · re-assign speakers">
        <p className="muted">
          Cuts over-long lines at sentence ends using the text you already have. Only the split lines get speakers from
          the saved detection; other lines keep theirs. Nothing is transcribed or detected again.
        </p>
        <div className="setting-list review-toggles">
          <Field
            label="Align to audio"
            help="Times the pieces from the audio with the Qwen3 forced aligner (a background job that can use the GPU). Off, the cuts are estimated from text length. If the aligner or audio is missing, estimated timing is used and you are told."
          >
            <Toggle checked={align} onChange={setAlign} />
          </Field>
        </div>
        <div className="actions">
          <button type="button" disabled={blocked} onClick={() => run(false)}>
            {busy || running ? 'Splitting…' : 'Re-split long lines'}
          </button>
          <button type="button" disabled={blocked} onClick={reassign}>
            Re-assign speakers from saved detection
          </button>
        </div>
        {speakers && (
          <div data-testid="speaker-time" className="resplit-speakers">
            <ul aria-label="Speaking time per speaker" className="muted">
              {speakerTimeLines(speakers).map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
            <p className="muted">{speakerTimeFooter(speakers)}</p>
          </div>
        )}
        {wait && !busy && <p className="muted" data-testid="resplit-wait">{wait}</p>}
        {needsConfirm && (
          <div className="reseg-ai-warn" role="alert" data-testid="resplit-confirm">
            <p>Some long lines already have English. Their pieces can't share it, so splitting clears the English on those lines only.</p>
            <button type="button" disabled={blocked} onClick={() => run(true)}>Split anyway</button>
          </div>
        )}
        {summary && <p role="status" data-testid="resplit-summary">{summary}</p>}
        {structureErrorText(error) ? (
          <p className="error" role="alert">{structureErrorText(error)}</p>
        ) : (
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        )}
      </Section>
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
