import { useEffect, useState, type ReactNode } from 'react'

import { artifactUrl } from '../../../api/client'
import {
  getArtifactInfo,
  getEpub,
  markExported,
  startAudiobook,
  startBurnedVideo,
  startDubbedVideo,
  startSoftsubVideo,
} from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import { jobSucceeded } from '../../../types/jobs'
import type {
  ArtifactInfo,
  AssExportRequest,
  MediaExportStarted,
  MediaKind,
  SoftsubVideoRequest,
} from '../../../types/export'
import { formatBytes } from '../exportForm'
import { mediaExportJobId } from '../stageJobIds'
import { useStage } from '../StageContext'
import { JobPanel } from './JobPanel'

export function ExportEpub() {
  const { dramaId } = useStage()
  const [field, setField] = useState<'en' | 'zh'>('en')
  const [error, setError] = useState<unknown>(null)

  const download = () =>
    getEpub(dramaId, field).then((blob) => {
      setError(null)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `drama_${dramaId}_${field}.epub`
      a.click()
      URL.revokeObjectURL(url)
    }, setError)

  return (
    <div className="export-block" role="group" aria-label="EPUB">
      <h4>EPUB (novel narration)</h4>
      <Field label="Language">
        <select value={field} onChange={(e) => setField(e.target.value as 'en' | 'zh')}>
          <option value="en">English</option>
          <option value="zh">Source language</option>
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary')} onClick={download}>Download EPUB</button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

interface JobProps {
  title: string
  label: string
  kind: MediaKind
  // Starts the job, or returns a plain-language reason it cannot start yet.
  start: () => Promise<MediaExportStarted> | string
  note: string
  // Distinguishes the download link when several sections share a kind.
  testId?: string
  children?: ReactNode
}

function MediaJobSection({ title, label, kind, start, note, testId, children }: JobProps) {
  const { dramaId } = useStage()
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  const [error, setError] = useState<unknown>(null)
  const [artifact, setArtifact] = useState<ArtifactInfo | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useReattachJob([mediaExportJobId(dramaId, kind)], adoptJob)

  // A file exported on an earlier visit stays downloadable; none yet (404) shows nothing.
  useEffect(() => {
    let cancelled = false
    getArtifactInfo(dramaId, kind).then((a) => !cancelled && setArtifact(a), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId, kind])

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (j.status !== 'done') return
      getArtifactInfo(dramaId, kind).then(setArtifact, setError)
    },
  })
  const busy = jobId !== null && !done && !pollError
  const busyId = `export-busy-${testId ?? kind}`

  const run = () => {
    const p = start()
    setArtifact(null)
    if (typeof p === 'string') {
      setProblem(p)
      return
    }
    setProblem(null)
    p.then(
      (r) => {
        setError(null)
        setJobId(r.job_id)
      },
      setError,
    )
  }

  return (
    <div className="export-block" role="group" aria-label={title}>
      <h4>{title}</h4>
      {note && <p className="muted">{note}</p>}
      {children}
      <button
        type="button"
        className={buttonClass('secondary')}
        disabled={busy}
        aria-describedby={busy ? busyId : undefined}
        onClick={run}
      >
        {label}
      </button>
      {busy && <p className="muted" id={busyId}>This export is running. Progress is shown below.</p>}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
      {artifact && (jobId === null || jobSucceeded(job)) && (
        <p data-testid={`artifact-${testId ?? kind}`}>
          <a href={artifactUrl(dramaId, kind)} download>Download {artifact.name}</a>{' '}
          <span className="muted">({formatBytes(artifact.size)})</span>
        </p>
      )}
    </div>
  )
}

export function ExportMediaJobs({ request }: { request: () => { request?: AssExportRequest; error?: string } }) {
  const { dramaId } = useStage()
  return (
    <>
      <MediaJobSection
        title="Audiobook"
        label="Start audiobook export"
        kind="audio"
        note="Encodes the narration audio to an .m4b file with chapter markers. Needs a finished narration track (Dub stage) and ffmpeg."
        start={() => startAudiobook(dramaId)}
      />
      <MediaJobSection
        title="Burned-in video"
        label="Start burned-in video export"
        kind="video"
        note="Burns the subtitles into the source video using the ASS style. Needs an uploaded source video and ffmpeg. This can take a while."
        start={() => {
          const r = request()
          return r.request ? startBurnedVideo(dramaId, r.request) : (r.error ?? 'Fix the ASS style settings first.')
        }}
      />
      <SoftsubVideo />
      <DubbedVideo />
    </>
  )
}

// Parity E17: the subtitles as a track viewers can switch on and off.
function SoftsubVideo() {
  const { dramaId } = useStage()
  const [field, setField] = useState<SoftsubVideoRequest['field']>('en')
  return (
    <MediaJobSection
      title="Video with a subtitle track"
      label="Start subtitle-track video export"
      kind="softsub_video"
      testId="softsub"
      note="Adds the subtitles as a track the viewer can turn on and off; the picture and sound are copied unchanged. MP4 and MKV keep their format, others become MP4. Needs an uploaded source video and ffmpeg."
      start={() => startSoftsubVideo(dramaId, { field })}
    >
      <Field label="Subtitles">
        <select value={field} onChange={(e) => setField(e.target.value as SoftsubVideoRequest['field'])}>
          <option value="en">English</option>
          <option value="bilingual">Bilingual</option>
          <option value="zh">Source language</option>
        </select>
      </Field>
    </MediaJobSection>
  )
}

// Parity E19: the dub track in place of (or over) the original audio.
function DubbedVideo() {
  const { dramaId } = useStage()
  const [keepOriginal, setKeepOriginal] = useState(false)
  return (
    <MediaJobSection
      title="Video with the dub audio"
      label="Start dubbed video export"
      kind="dubbed_video"
      testId="dubbed"
      note="Replaces the video's sound with the dub track from the Dub stage. Needs an uploaded source video, a finished dub and ffmpeg."
      start={() => startDubbedVideo(dramaId, { keep_original: keepOriginal })}
    >
      <div className="setting-list">
        <Field label="Mix the original audio in quietly underneath">
          <Toggle checked={keepOriginal} onChange={setKeepOriginal} />
        </Field>
      </div>
    </MediaJobSection>
  )
}

// Parity E22: Streamlit's "Mark as exported" (sets the drama's status only).
export function MarkExported() {
  const { dramaId, drama, refetchDrama } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)
  const exported = done || drama.status === 'exported'
  const mark = () => {
    setPending(true)
    markExported(dramaId)
      .then(
        () => {
          setError(null)
          setDone(true)
          refetchDrama()
        },
        setError,
      )
      .finally(() => setPending(false))
  }
  return (
    <div className="export-actions" data-testid="mark-exported">
      {exported ? (
        <span className="muted" role="status">This drama is marked as exported.</span>
      ) : (
        <>
          <button type="button" className={buttonClass('secondary')} disabled={pending} onClick={mark}>
            Mark as exported
          </button>
          <span className="muted">Sets the Library status only; nothing else changes.</span>
        </>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
