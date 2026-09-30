import { useEffect, useRef, useState } from 'react'

import { getJob } from '../../../api/jobs'
import { removeMedia } from '../../../api/stageDeletes'
import { getMediaStatus, uploadMedia } from '../../../api/workspace'
import { Badge } from '../../../components/Badge'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { MediaStatus } from '../../../types/workspace'
import { TERMINAL_STATUSES } from '../../../types/jobs'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../../../hooks/usePcOnly'
import { usePersistedState } from '../../../hooks/usePersistedState'
import { checkUploadFile, sourceJobIds } from '../sourceForm'
import { useStage } from '../StageContext'
import { CreditsCoverPanel } from './CreditsCoverPanel'
import { DetailsPanel, SourceModePanel } from './DetailsPanel'
import { AnalyzePanel, AutofillPanel } from './MetadataPanel'
import { ResearchPanel } from './ResearchPanel'
import { JobPanel } from './JobPanel'
import { NovelGlossary } from './NovelGlossary'
import { NovelPanel } from './NovelPanel'
import TranscribeStage from './TranscribeStage'
import { UrlDownload } from './UrlDownload'
import { mediaFileInputId } from './stageBlockers'

export default function SourceStage() {
  const { dramaId, drama, onJobDone } = useStage()
  const [media, setMedia] = useState<MediaStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const [reloads, setReloads] = useState(0)
  // Bumped when the transcript mode changes so the Transcribe panel re-reads its config.
  const [modeVersion, setModeVersion] = useState(0)
  const pc = usePcOnly()
  const [removeError, setRemoveError] = useState<unknown>(null)
  // "Upload a file" or "From a URL", remembered per viewer.
  const [from, setFrom] = usePersistedState<'file' | 'url'>('source.mediaFrom', 'file')
  const fromUrl = from === 'url'

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
    setRemoveError(null)
    removeMedia(dramaId).then(
      () => {
        setError(null)
        setUploaded('Removed. Lines are untouched.')
        setReloads((n) => n + 1)
        onJobDone()
      },
      setRemoveError,
    )
  }

  const mediaSlot = (
    <>
      {media && (
        <p className="source-media-status" data-testid="media-status">
          <Badge tone={media.has_audio ? 'ok' : 'neutral'}>{media.has_audio ? 'Audio attached' : 'No audio'}</Badge>
          <Badge tone={media.has_source_video ? 'ok' : 'neutral'}>
            {media.has_source_video ? 'Source video attached' : 'No source video'}
          </Badge>
          <span className="muted">limit {media.upload_max_mb} MB</span>
        </p>
      )}
      <div className="segmented source-from" role="radiogroup" aria-label="Get audio or video">
        <label className={!fromUrl ? 'segmented-on' : undefined}>
          <input type="radio" name={`source-from-${dramaId}`} checked={!fromUrl} onChange={() => setFrom('file')} />
          Upload a file
        </label>
        <label className={fromUrl ? 'segmented-on' : undefined}>
          <input type="radio" name={`source-from-${dramaId}`} checked={fromUrl} onChange={() => setFrom('url')} />
          From a URL
        </label>
      </div>
      {fromUrl ? (
        media && (
          <UrlDownload
            key={dramaId}
            dramaId={dramaId}
            contentMode={drama.content_mode}
            hasAudio={media.has_audio}
            busy={busy}
            onStarted={(id) => {
              setUploaded(null)
              setJobId(id)
            }}
          />
        )
      ) : (
        <div className="source-file">
          <input
            type="file"
            id={mediaFileInputId(dramaId)}
            aria-label="Audio or video file"
            accept=".mp3,.wav,.m4a,.flac,.ogg,.mp4,.mkv,.mov,.webm"
            disabled={!media}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
          <button type="button" className={buttonClass('secondary')} disabled={!file || busy} onClick={upload}>
            Upload
          </button>
        </div>
      )}
      {hasMedia && pc === 'local' && (
        <div className="actions">
          <ConfirmButton
            name="audio/video"
            label="Remove…"
            confirmLabel="Confirm remove audio/video"
            disabled={busy}
            onConfirm={remove}
          />
          {busy && <span className="muted">Wait for the running job to finish.</span>}
        </div>
      )}
      {hasMedia && pc === 'remote' && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
      <ErrorBanner error={removeError} describe={{ pcOnly: true }} onDismiss={() => setRemoveError(null)} />
      {fileProblem && <p className="error" role="alert">{fileProblem}</p>}
      {uploaded && <p role="status">{uploaded}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )

  return (
    <div className="stage-source">
      <TranscribeStage key={modeVersion} mediaSlot={mediaSlot} media={media} file={file} busy={busy} onJobStarted={setJobId} />
      <NovelPanel busy={busy} onOcrStarted={setJobId} reloadKey={reloads} />
      <section className="panel" aria-label="Glossary from novel">
        <NovelGlossary title="Glossary from novel" storageKey="source.glossary.novel" />
      </section>
      <SourceModePanel onSaved={() => setModeVersion((n) => n + 1)} />
      <DetailsPanel />
      <CreditsCoverPanel />
      <AutofillPanel />
      <ResearchPanel />
      <AnalyzePanel hasMedia={hasMedia} />
      {jobId && <JobPanel job={job} pollError={pollError} />}
    </div>
  )
}
