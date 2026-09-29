import { useEffect, useRef, useState } from 'react'

import { getJob } from '../../../api/jobs'
import { removeMedia } from '../../../api/stageDeletes'
import { getMediaStatus, uploadMedia } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { MediaStatus } from '../../../types/workspace'
import { TERMINAL_STATUSES } from '../../../types/jobs'
import { ConfirmButton } from '../pcOnly/ConfirmButton'
import { PC_ONLY_NOTE, reportPcOnlyError, usePcOnly } from '../pcOnly/pcOnly'
import { checkUploadFile, sourceJobIds } from '../sourceForm'
import { useStage } from '../StageContext'
import { DetailsPanel, SourceModePanel } from './DetailsPanel'
import { AnalyzePanel, AutofillPanel } from './MetadataPanel'
import { JobPanel } from './JobPanel'
import { NovelPanel } from './NovelPanel'
import TranscribeStage from './TranscribeStage'

export default function SourceStage() {
  const { dramaId, onJobDone } = useStage()
  const [media, setMedia] = useState<MediaStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const [reloads, setReloads] = useState(0)
  // Bumped when the transcript mode changes so the Transcribe panel re-reads its config.
  const [modeVersion, setModeVersion] = useState(0)
  const isLocal = usePcOnly()
  const [removeProblem, setRemoveProblem] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getMediaStatus(dramaId).then(
      (m) => !cancelled && setMedia(m),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const startedRef = useRef(false)
  useEffect(() => {
    startedRef.current = jobId !== null
  })

  // Reattach to a run started before this stage was left/reloaded: the job
  // keeps running server-side. 404 or a finished job means nothing to show.
  useEffect(() => {
    let cancelled = false
    for (const id of sourceJobIds(dramaId)) {
      getJob(id).then(
        (j) => {
          if (!cancelled && !startedRef.current && !TERMINAL_STATUSES.includes(j.status)) setJobId(id)
        },
        () => undefined,
      )
    }
    return () => {
      cancelled = true
    }
  }, [dramaId, setJobId])

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => {
      onJobDone()
      setReloads((n) => n + 1)
    },
  })
  const busy = jobId !== null && !done && !pollError

  const pick = (f: File | null) => {
    setUploaded(null)
    const problem = f && media ? checkUploadFile(f.name, f.size, media.upload_max_mb) : null
    setFileProblem(problem)
    setFile(f && !problem ? f : null)
  }

  const upload = () => {
    if (!file) return
    uploadMedia(dramaId, file).then(
      (r) => {
        setError(null)
        const mb = (r.size / (1024 * 1024)).toFixed(1)
        setFile(null)
        if (r.job_id) {
          // Video: audio extraction runs as a job; the panel reloads when it finishes.
          setUploaded(`Uploaded video (${mb} MB). Extracting audio...`)
          setJobId(r.job_id)
          return
        }
        setUploaded(`Uploaded ${r.kind} (${mb} MB).`)
        setReloads((n) => n + 1)
        onJobDone()
      },
      setError,
    )
  }

  const hasMedia = !!media && (media.has_audio || media.has_source_video)
  const remove = () => {
    setUploaded(null)
    setRemoveProblem(null)
    removeMedia(dramaId).then(
      () => {
        setError(null)
        setUploaded('Removed. Lines are untouched.')
        setReloads((n) => n + 1)
        onJobDone()
      },
      (e: unknown) => reportPcOnlyError(e, setRemoveProblem, setError),
    )
  }

  const mediaSlot = (
    <>
      {media && (
        <p className="muted" data-testid="media-status">
          Audio: {media.has_audio ? 'attached' : 'none'} · Source video:{' '}
          {media.has_source_video ? 'attached' : 'none'} · limit {media.upload_max_mb} MB
        </p>
      )}
      <div className="source-file">
        <input
          type="file"
          aria-label="Audio or video file"
          accept=".mp3,.wav,.m4a,.flac,.ogg,.mp4,.mkv,.mov,.webm"
          disabled={!media}
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
        <button type="button" disabled={!file || busy} onClick={upload}>
          Upload
        </button>
      </div>
      {hasMedia && isLocal && (
        <ConfirmButton
          label="Remove audio/video"
          confirmLabel="Confirm remove audio/video"
          disabled={busy}
          disabledReason="Wait for the running job to finish."
          onConfirm={remove}
        />
      )}
      {hasMedia && isLocal === false && <p className="muted">{PC_ONLY_NOTE}</p>}
      {removeProblem && <p className="error" role="alert">{removeProblem}</p>}
      {fileProblem && <p className="error" role="alert">{fileProblem}</p>}
      {uploaded && <p role="status">{uploaded}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )

  return (
    <div className="stage-source">
      <TranscribeStage key={modeVersion} mediaSlot={mediaSlot} media={media} file={file} busy={busy} onJobStarted={setJobId} />
      <NovelPanel busy={busy} onOcrStarted={setJobId} reloadKey={reloads} />
      <SourceModePanel onSaved={() => setModeVersion((n) => n + 1)} />
      <DetailsPanel />
      <AutofillPanel />
      <AnalyzePanel hasMedia={hasMedia} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
