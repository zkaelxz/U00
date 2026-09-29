import { useCallback, useEffect, useState } from 'react'

import { getSenseVoice, startSenseVoice } from '../../../../api/reviewExtras'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { lineNumber } from '../../../../lineNumber'
import type { SenseVoiceTags } from '../../../../types/reviewExtras'
import { useStage } from '../../StageContext'
import { JobPanel } from '../JobPanel'
import { senseVoiceSummary } from './aiExtrasLogic'

// One-word tags stay whole; the table scrolls sideways inside .table-scroll.
const NOWRAP = { whiteSpace: 'nowrap' } as const

interface Props {
  dramaId: number
  reloads: number
}

// SenseVoice: emotion and sounds read from the audio, shown next to the
// text-based emotion tags. Never merged into them; only the text-based tags
// feed translation.
export function AiExtrasSenseVoice({ dramaId, reloads }: Props) {
  const { onJobDone } = useStage()
  const [tags, setTags] = useState<SenseVoiceTags | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const [loadKey, setLoadKey] = useState(0)
  const reload = useCallback(() => setLoadKey((n) => n + 1), [])
  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      onJobDone()
      reload()
    },
  })
  const running = jobId !== null && !done && !pollError

  useEffect(() => {
    let cancelled = false
    getSenseVoice(dramaId).then(
      (t) => !cancelled && setTags(t),
      (e) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads, loadKey])

  const start = () => {
    setError(null)
    startSenseVoice(dramaId).then((r) => setJobId(r.job_id), setError)
  }

  const reason = !tags
    ? null
    : !tags.installed
      ? 'Needs the optional funasr package on the PC (pip install funasr).'
      : !tags.has_audio
        ? 'This project has no audio to listen to.'
        : null

  return (
    <Section storageKey="review.aiExtras.sensevoice" title="Audio tags (SenseVoice)" summary={tags ? senseVoiceSummary(tags) : 'Loading…'}>
      <p className="muted">
        A second opinion from how each line sounds: emotion plus laughter, crying or music. Shown beside the text-based
        tags, never merged into them.
      </p>
      <div className="actions">
        <button type="button" disabled={!tags || !!reason || running} onClick={start} aria-describedby={reason ? 'sensevoice-reason' : undefined}>
          {running ? 'Listening…' : tags && tags.tagged > 0 ? 'Tag again' : 'Tag from the audio'}
        </button>
        {reason && (
          <span id="sensevoice-reason" className="muted">
            {reason}
          </span>
        )}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
      {tags && <p data-testid="sensevoice-summary">{senseVoiceSummary(tags)}</p>}
      {tags && tags.rows.length > 0 && (
        <div className="table-scroll">
          <table data-testid="sensevoice-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Line</th>
                <th scope="col">Text-based</th>
                <th scope="col">Audio</th>
                <th scope="col">Sounds</th>
                <th scope="col">Disagree</th>
              </tr>
            </thead>
            <tbody>
              {tags.rows.map((r) => (
                <tr key={r.line_id ?? `i${r.idx}`}>
                  <td>{lineNumber(r.idx)}</td>
                  <td lang="zh">{r.text}</td>
                  <td style={NOWRAP}>{r.text_emotion || '–'}</td>
                  <td style={NOWRAP}>{r.audio_emotion || '–'}</td>
                  <td>{r.audio_events || '–'}</td>
                  <td>{r.disagree ? 'Yes' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {tags && <p className="muted">{tags.license_note}</p>}
    </Section>
  )
}
