import { useEffect, useState } from 'react'

import { getJob } from '../../../api/jobs'
import { getMediaStatus, uploadMedia } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { useJob } from '../../../hooks/useJob'
import type { MediaStatus } from '../../../types/workspace'
import { TERMINAL_STATUSES } from '../../../types/jobs'
import { checkUploadFile, sourceJobIds } from '../sourceForm'
import { useStage } from '../StageContext'
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
  const [jobId, setJobId] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

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

  // Reattach to a run started before this stage was left/reloaded: the job
  // keeps running server-side. 404 or a finished job means nothing to show.
  useEffect(() => {
    let cancelled = false
    for (const id of sourceJobIds(dramaId)) {
      getJob(id).then(
        (j) => {
          if (!cancelled && !TERMINAL_STATUSES.includes(j.status)) setJobId((cur) => cur ?? id)
        },
        () => undefined,
      )
    }
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const { job, done, error: pollError } = useJob(jobId, {
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
        setUploaded(`Uploaded ${r.kind} (${(r.size / (1024 * 1024)).toFixed(1)} MB).`)
        setFile(null)
        setReloads((n) => n + 1)
        onJobDone()
      },
      setError,
    )
  }

  return (
    <div className="stage-source">
      <section className="panel" aria-label="Media">
        <h3>Media</h3>
        {media && (
          <p className="muted" data-testid="media-status">
            Audio: {media.has_audio ? 'attached' : 'none'} · Source video:{' '}
            {media.has_source_video ? 'attached' : 'none'} · Upload limit {media.upload_max_mb} MB
          </p>
        )}
        <label>
          Audio or video file
          <input
            type="file"
            aria-label="Audio or video file"
            accept=".mp3,.wav,.m4a,.flac,.ogg,.mp4,.mkv,.mov,.webm"
            disabled={!media}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
        </label>
        <button type="button" disabled={!file || busy} onClick={upload}>
          Upload
        </button>
        {fileProblem && <p className="error" role="alert">{fileProblem}</p>}
        {uploaded && <p role="status">{uploaded}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </section>
      <TranscribeStage file={file} busy={busy} onJobStarted={setJobId} />
      <NovelPanel />
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
