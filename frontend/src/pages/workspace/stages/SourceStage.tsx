import { useEffect, useState } from 'react'

import { removeMedia } from '../../../api/stageDeletes'
import { getMediaStatus, uploadMedia } from '../../../api/workspace'
import { Badge } from '../../../components/Badge'
import { formatBytes } from '../../diskUsage/diskUsageModel'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import type { MediaStatus } from '../../../types/workspace'
import { Section } from '../../../components/Section'
import { writeSectionOpen } from '../../../components/sectionStorage'
import { wantsAutofill } from '../../libraryParity/libraryParity'
import { mediaKind } from '../detailsForm'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../../../hooks/usePcOnly'
import { usePersistedState } from '../../../hooks/usePersistedState'
import { checkUploadFile, sourceJobIds, UPLOAD_EXTENSIONS } from '../sourceForm'
import { useStage } from '../StageContext'
import { DetailsPanel } from './DetailsPanel'
import { FillInPanel } from './MetadataPanel'
import { JobPanel } from './JobPanel'
import { NovelPanel } from './NovelPanel'
import TranscribeStage from './TranscribeStage'
import { UrlDownload } from './UrlDownload'
import { mediaFileInputId, needsReplaceConfirm, replaceBoxId } from './stageBlockers'

// Nothing clears kept_media on its own, so say how much it holds wherever a
// replace or remove would add to it or might be expected to free it.
const keptMediaNote = (m: MediaStatus | null) =>
  m && m.kept_media_files > 0
    ? `Old copies kept in this title's folder: ${m.kept_media_files} file${m.kept_media_files === 1 ? '' : 's'}, ` +
      `${formatBytes(m.kept_media_bytes)}. Removing audio/video doesn't delete them; clear them in Library tools → Disk usage.`
    : null

export default function SourceStage() {
  const { dramaId, drama, onJobDone } = useStage()
  const [media, setMedia] = useState<MediaStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [replace, setReplace] = useState(false)
  // The server says the drama has audio/video even if the status we read did not.
  const [serverHasMedia, setServerHasMedia] = useState(false)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  const [expectedSeconds, setExpectedSeconds] = useState<number | null>(null)
  const [reloads, setReloads] = useState(0)
  const pc = usePcOnly()
  const [removeError, setRemoveError] = useState<unknown>(null)
  // "Upload a file" or "From a URL", remembered per viewer.
  const [from, setFrom] = usePersistedState<'file' | 'url'>('source.mediaFrom', 'file')
  const fromUrl = from === 'url'

  // Arriving from Library "Create and auto-fill" must show the Auto-fill panel,
  // which lives in the collapsed "Details and credits" group's Fill in details fold.
  useState(() => {
    if (wantsAutofill(window.location.hash)) {
      try {
        writeSectionOpen(window.localStorage, 'source.group.details', true)
      } catch {
        // storage unavailable: the group stays closed
      }
    }
  })
  // Buttons next to a "Still needed" line reveal the section that resolves it.
  const [revealDetails, setRevealDetails] = useState(0)
  const [revealMedia, setRevealMedia] = useState(0)
  const addCredits = () => {
    setRevealDetails((n) => n + 1)
    setTimeout(() => {
      const el = document.querySelector<HTMLElement>('[aria-label="Edit details"] input[name="author"]')
      el?.scrollIntoView({ block: 'center' })
      el?.focus()
    }, 50)
  }
  const needMedia = () => {
    setRevealMedia((n) => n + 1)
    setTimeout(() => {
      const el = document.getElementById(mediaFileInputId(dramaId))
      el?.scrollIntoView({ block: 'center' })
      el?.focus()
    }, 50)
  }

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

  // Reattach to a run started before this stage was left/reloaded.
  useReattachJob(sourceJobIds(dramaId), adoptJob)

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

  const hasMedia = !!media && (media.has_audio || media.has_source_video)
  const mustConfirm = hasMedia || serverHasMedia
  const confirmReplace = mustConfirm && replace

  const upload = () => {
    if (!file || (mustConfirm && !replace)) return
    uploadMedia(dramaId, file, confirmReplace).then(
      (r) => {
        setError(null)
        setReplace(false)
        setServerHasMedia(false)
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
      (e: unknown) => {
        if (needsReplaceConfirm(e)) setServerHasMedia(true)
        setError(e)
      },
    )
  }

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
          <span className="muted">Limit {media.upload_max_mb} MB</span>
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
            accept={UPLOAD_EXTENSIONS.join(',')}
            disabled={!media}
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={!file || busy || (mustConfirm && !replace)}
            onClick={upload}
          >
            Upload
          </button>
          {mustConfirm && (
            <label>
              <input type="checkbox" id={replaceBoxId(dramaId)} checked={replace} onChange={(e) => setReplace(e.target.checked)} />
              Replace the current audio/video (the old file is kept in this title's folder)
            </label>
          )}
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
      {(mustConfirm || hasMedia) && keptMediaNote(media) && (
        <p className="muted" data-testid="kept-media">{keptMediaNote(media)}</p>
      )}
      <ErrorBanner error={removeError} describe={{ pcOnly: true }} onDismiss={() => setRemoveError(null)} />
      {fileProblem && <p className="error" role="alert">{fileProblem}</p>}
      {uploaded && <p role="status">{uploaded}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )

  // The workflow for the drama's media type comes first and opens by default.
  const kind = mediaKind(drama.media_type)
  const transcribe = (
    <Section
      key="transcribe"
      storageKey="source.group.transcribe"
      defaultOpen={kind === 'audio'}
      openSignal={revealMedia}
      title="Transcribe audio or video"
      summary={hasMedia ? 'audio attached' : 'upload or download a file'}
    >
      <TranscribeStage mediaSlot={mediaSlot} media={media} file={file} confirmReplace={confirmReplace}
        replaceUnconfirmed={mustConfirm && !replace} onReplaceRefused={() => setServerHasMedia(true)} busy={busy}
        onJobStarted={(id, expected, sentFile) => {
          // Otherwise every later run would upload the same file again and keep another full copy.
          if (sentFile) {
            setFile(null)
            setReplace(false)
            setServerHasMedia(false)
          }
          setExpectedSeconds(expected ?? null)
          setJobId(id)
        }}
      />
    </Section>
  )
  const novel = (
    <div key="novel" className="source-group">
      <NovelPanel busy={busy} onOcrStarted={setJobId} reloadKey={reloads} kind={kind} primary={kind !== 'audio'} />
    </div>
  )
  const groups = kind === 'audio' ? [transcribe, novel] : [novel, transcribe]

  return (
    <div className="stage-source">
      {groups}
      <Section
        storageKey="source.group.details"
        openSignal={revealDetails}
        title="Details and credits"
        summary={`${drama.title_en || drama.title_zh || `#${dramaId}`} · credits, cover, fill in`}
      >
        <DetailsPanel openSignal={revealDetails} onAddCredits={addCredits} />
        <FillInPanel hasMedia={hasMedia} onNeedMedia={needMedia} />
      </Section>
      {jobId && <JobPanel job={job} pollError={pollError} liveEta={jobId.startsWith('transcribe_')} expectedSeconds={expectedSeconds} />}
    </div>
  )
}
