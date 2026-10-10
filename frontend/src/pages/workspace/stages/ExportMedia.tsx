import { useEffect, useId, useState, type ReactNode } from 'react'

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
import { writeSectionOpen } from '../../../components/sectionStorage'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { getWorkflowProgress } from '../../../api/workspace'
import { browserStorage } from '../../../hooks/usePersistedState'
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
import { formatBytes, mediaBlockOpen, mediaBlockStorageKey } from '../exportForm'
import { mediaExportJobId } from '../stageJobIds'
import { useStage } from '../StageContext'
import { JobPanel } from './JobPanel'

const NO_NARRATION = 'There is no narration yet. Create it in Dub first.'
const NO_DUB = 'There is no dub yet. Create it in Dub first.'

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
  // A reason already known up front: the button stays disabled and shows it.
  blockedReason?: string | null
  // One start error is shown for the whole list, so the same failure never stacks.
  startError: StartError | null
  onStartError: (error: StartError | null) => void
  note: string
  // Distinguishes the download link when several sections share a kind.
  testId?: string
  children?: ReactNode
}

function MediaJobSection({ title, label, kind, start, blockedReason, startError, onStartError, note, testId, children }: JobProps) {
  const { dramaId } = useStage()
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
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
      getArtifactInfo(dramaId, kind).then(setArtifact, (e) => onStartError({ kind: testId ?? kind, error: e }))
    },
  })
  // Covers the gap between the click and the job id arriving, when a second click would start a second export.
  const [starting, setStarting] = useState(false)
  const busy = starting || (jobId !== null && !done && !pollError)
  const busyId = `export-busy-${testId ?? kind}`
  const blockedId = `export-blocked-${testId ?? kind}`
  const blocked = blockedReason ?? null
  const bodyId = useId()
  const [open, setOpen] = useState(() => mediaBlockOpen(browserStorage(), kind))
  const toggle = () => {
    writeSectionOpen(browserStorage(), mediaBlockStorageKey(kind), !open)
    setOpen(!open)
  }

  const run = () => {
    if (busy) return
    const p = start()
    setArtifact(null)
    if (typeof p === 'string') {
      setProblem(p)
      return
    }
    setProblem(null)
    setStarting(true)
    p.then(
      (r) => {
        onStartError(null)
        setJobId(r.job_id)
        setStarting(false)
      },
      (e) => {
        onStartError({ kind: testId ?? kind, error: e })
        setStarting(false)
      },
    )
  }

  return (
    <div className="export-block" role="group" aria-label={title}>
      <h4 className="export-block-heading">
        <button type="button" className="export-block-toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
          {title}
        </button>
      </h4>
      {/* Hidden, not unmounted: the job keeps polling and its fields keep their values while folded. */}
      <div className="export-block-body" id={bodyId} hidden={!open}>
        {note && <p className="muted">{note}</p>}
        {children}
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={busy || blocked !== null}
          aria-describedby={busy ? busyId : blocked ? blockedId : undefined}
          onClick={run}
        >
          {label}
        </button>
      </div>
      {busy && <p className="muted" id={busyId}>This export is running. Progress is shown below.</p>}
      {blocked && !busy && <p className="muted" id={blockedId}>{blocked}</p>}
      {problem && <p className="error" role="alert">{problem}</p>}
      {startError?.kind === (testId ?? kind) && (
        <ErrorBanner error={startError.error} describe={{ reasonAsTitle: true }} onDismiss={() => onStartError(null)} />
      )}
      <JobPanel jobId={jobId} job={job} pollError={pollError} lastRun={{ dramaId, ids: [mediaExportJobId(dramaId, kind)], retryFor: () => run }} />
      {artifact && (jobId === null || jobSucceeded(job)) && (
        <p data-testid={`artifact-${testId ?? kind}`}>
          <a href={artifactUrl(dramaId, kind)} download>Download {artifact.name}</a>{' '}
          <span className="muted">({formatBytes(artifact.size)})</span>
        </p>
      )}
    </div>
  )
}

interface StartError {
  kind: string
  error: unknown
}

export function ExportMediaJobs({ request }: { request: () => { request?: AssExportRequest; error?: string } }) {
  const { dramaId } = useStage()
  const [startError, setStartError] = useState<StartError | null>(null)
  // Advisory: if the state cannot be loaded the buttons stay enabled and the server's own reason shows.
  const [tracks, setTracks] = useState<{ narration: boolean; dub: boolean } | null>(null)
  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(dramaId).then(
      (p) => !cancelled && setTracks({ narration: p.has_narration_track === true, dub: p.has_dub_track }),
      () => !cancelled && setTracks(null),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])
  const shared = { startError, onStartError: setStartError }
  return (
    <>
      <MediaJobSection
        title="Audiobook"
        label="Start audiobook export"
        kind="audio"
        note="Encodes the narration audio to an .m4b file with chapter markers. Needs a finished narration track (Dub stage) and ffmpeg."
        start={() => startAudiobook(dramaId)}
        blockedReason={tracks && !tracks.narration ? NO_NARRATION : null}
        {...shared}
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
        {...shared}
      />
      <SoftsubVideo {...shared} />
      <DubbedVideo {...shared} blockedReason={tracks && !tracks.dub ? NO_DUB : null} />
    </>
  )
}

// Parity E17: the subtitles as a track viewers can switch on and off.
type SectionShare = Pick<JobProps, 'startError' | 'onStartError'>

function SoftsubVideo(share: SectionShare) {
  const { dramaId } = useStage()
  const [field, setField] = useState<SoftsubVideoRequest['field']>('en')
  return (
    <MediaJobSection
      title="Video with a subtitle track"
      label="Start subtitle-track video export"
      kind="softsub_video"
      testId="softsub"
      note="Adds the subtitles as a track the viewer can turn on and off; the picture and sound are copied unchanged. MP4 and MKV keep their format, anything else becomes MKV. Needs an uploaded source video and ffmpeg."
      start={() => startSoftsubVideo(dramaId, { field })}
      {...share}
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
function DubbedVideo({ blockedReason, ...share }: SectionShare & Pick<JobProps, 'blockedReason'>) {
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
      blockedReason={blockedReason}
      {...share}
    >
      <div className="setting-list">
        <Field label="Mix the original audio in quietly underneath">
          <Toggle checked={keepOriginal} onChange={setKeepOriginal} />
        </Field>
      </div>
    </MediaJobSection>
  )
}

// "Mark as exported" (sets the drama's status only).
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
        <span className="muted" role="status">This title is marked as exported.</span>
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
