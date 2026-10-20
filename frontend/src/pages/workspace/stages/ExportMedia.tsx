import { useState, type ReactNode } from 'react'

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
import { downloadBlob } from '../../../components/downloadBlob'
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
  ExportMediaKind,
  SoftsubVideoRequest,
} from '../../../types/export'
import { formatBytes } from '../exportForm'
import type { ExportBlockId } from '../exportBlocks'
import { mediaExportJobId } from '../stageJobIds'
import { useStage } from '../StageContext'
import { ExportBlock } from './ExportBlock'
import { JobPanel } from './JobPanel'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'

export function ExportEpub({ reason }: { reason: string | null }) {
  const { dramaId } = useStage()
  const [field, setField] = useState<'en' | 'zh'>('en')
  const [error, setError] = useState<unknown>(null)

  const download = () =>
    getEpub(dramaId, field).then((blob) => {
      setError(null)
      downloadBlob(blob, `drama_${dramaId}_${field}.epub`)
    }, setError)

  return (
    <ExportBlock id="epub" reason={reason} hint="Novel narration">
      <Field label="Language">
        <select value={field} onChange={(e) => setField(e.target.value as 'en' | 'zh')}>
          <option value="en">English</option>
          <option value="zh">Source language</option>
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary')} disabled={reason !== null} onClick={download}>Download EPUB</button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </ExportBlock>
  )
}

interface JobProps {
  id: ExportBlockId
  label: string
  kind: ExportMediaKind
  // Starts the job, or returns a plain-language reason it cannot start yet.
  start: () => Promise<MediaExportStarted> | string
  // A reason already known up front: the button stays disabled and shows it.
  blockedReason?: string | null
  startError: StartError | null
  onStartError: (error: StartError | null) => void
  note: string
  // Shown beside the title while the block is folded and nothing blocks it.
  hint: string
  // Distinguishes the download link when several sections share a kind.
  testId?: string
  children?: ReactNode
}

function MediaJobSection({ id, label, kind, start, blockedReason, startError, onStartError, note, hint, testId, children }: JobProps) {
  const { dramaId } = useStage()
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  const [artifact, setArtifact] = useState<ArtifactInfo | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useReattachJob([mediaExportJobId(dramaId, kind)], adoptJob)

  // A file exported on an earlier visit stays downloadable; none yet (404) shows nothing.
  useCancellableEffect((cancelled) => {
    getArtifactInfo(dramaId, kind).then((a) => !cancelled() && setArtifact(a), () => undefined)
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
    <ExportBlock
      id={id}
      reason={blocked}
      hint={hint}
      status={
        <>
          {busy && <p className="muted" id={busyId}>This export is running. Progress is shown below.</p>}
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
        </>
      }
    >
      {note && <p className="muted">{note}</p>}
      {children}
      {blocked && <p className="muted" id={blockedId}>{blocked}</p>}
      <button
        type="button"
        className={buttonClass('secondary')}
        disabled={busy || blocked !== null}
        aria-describedby={busy ? busyId : blocked ? blockedId : undefined}
        onClick={run}
      >
        {label}
      </button>
    </ExportBlock>
  )
}

export interface StartError {
  kind: string
  error: unknown
}

// One start error is shown for the whole list, so the same failure never stacks.
export interface BlockShare {
  startError: StartError | null
  onStartError: (error: StartError | null) => void
  reason: string | null
}

export function AudiobookBlock({ reason, startError, onStartError }: BlockShare) {
  const { dramaId } = useStage()
  return (
    <MediaJobSection
      id="audiobook"
      label="Start audiobook export"
      kind="audio"
      hint=".m4b with chapter markers"
      note="Encodes the narration audio to an .m4b file with chapter markers."
      start={() => startAudiobook(dramaId)}
      blockedReason={reason}
      startError={startError}
      onStartError={onStartError}
    />
  )
}

export function BurnedVideoBlock({ request, reason, startError, onStartError, children }: BlockShare & {
  request: () => { request?: AssExportRequest; error?: string }
  children?: ReactNode
}) {
  const { dramaId } = useStage()
  return (
    <MediaJobSection
      id="burned_video"
      label="Start burned-in video export"
      kind="video"
      hint="Subtitles drawn into the picture"
      note="Burns the subtitles into the source video using the ASS style. This can take a while."
      start={() => {
        const r = request()
        return r.request ? startBurnedVideo(dramaId, r.request) : (r.error ?? 'Fix the ASS style settings first.')
      }}
      blockedReason={reason}
      startError={startError}
      onStartError={onStartError}
    >
      {children}
    </MediaJobSection>
  )
}

// Parity E17: the subtitles as a track viewers can switch on and off.
export function SoftsubVideoBlock({ reason, startError, onStartError }: BlockShare) {
  const { dramaId } = useStage()
  const [field, setField] = useState<SoftsubVideoRequest['field']>('en')
  const [format, setFormat] = useState<NonNullable<SoftsubVideoRequest['subtitle_format']>>('srt')
  return (
    <MediaJobSection
      id="softsub_video"
      label="Start subtitle-track video export"
      kind="softsub_video"
      testId="softsub"
      hint="Subtitles the viewer can turn on and off"
      note="Adds the subtitles as a track the viewer can turn on and off; the picture and sound are copied unchanged. MP4 and MKV keep their format, anything else becomes MKV."
      start={() => startSoftsubVideo(dramaId, { field, subtitle_format: format })}
      blockedReason={reason}
      startError={startError}
      onStartError={onStartError}
    >
      <Field label="Subtitles">
        <select value={field} onChange={(e) => setField(e.target.value as SoftsubVideoRequest['field'])}>
          <option value="en">English</option>
          <option value="bilingual">Bilingual</option>
          <option value="zh">Source language</option>
        </select>
      </Field>
      <Field
        label="Subtitle format"
        help={
          format === 'ass'
            ? 'ASS keeps overlapping lines and speaker colours. It plays in VLC and mpv; some TVs and phone players ignore the styling. The video is saved as MKV, because MP4 cannot hold ASS styling.'
            : 'ASS keeps overlapping lines and speaker colours. It plays in VLC and mpv; some TVs and phone players ignore the styling.'
        }
      >
        <select value={format} onChange={(e) => setFormat(e.target.value as typeof format)}>
          <option value="srt">SRT (works everywhere)</option>
          <option value="ass">ASS (saved as MKV)</option>
        </select>
      </Field>
    </MediaJobSection>
  )
}

// Parity E19: the dub track in place of (or over) the original audio.
export function DubbedVideoBlock({ reason, startError, onStartError }: BlockShare) {
  const { dramaId } = useStage()
  const [keepOriginal, setKeepOriginal] = useState(false)
  return (
    <MediaJobSection
      id="dubbed_video"
      label="Start dubbed video export"
      kind="dubbed_video"
      testId="dubbed"
      hint="The dub track in place of the original sound"
      note="Replaces the video's sound with the dub track from the Dub stage."
      start={() => startDubbedVideo(dramaId, { keep_original: keepOriginal })}
      blockedReason={reason}
      startError={startError}
      onStartError={onStartError}
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
