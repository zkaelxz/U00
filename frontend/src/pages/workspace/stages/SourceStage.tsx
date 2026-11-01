import { useEffect, useRef, useState } from 'react'

import { updateDramaMetadata } from '../../../api/library'
import { removeMedia } from '../../../api/stageDeletes'
import { getMediaStatus, uploadMedia } from '../../../api/workspace'
import { Badge } from '../../../components/Badge'
import { ButtonLink } from '../../../components/Button'
import { formatDecimalBytes } from '../../diskUsage/diskUsageModel'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import type { MediaStatus } from '../../../types/workspace'
import { Section } from '../../../components/Section'
import { Tabs } from '../../../components/Tabs'
import { browserStorage } from '../../../hooks/usePersistedState'
import { parseRoute } from '../../../router'
import { wantsAutofill } from '../../libraryParity/libraryParity'
import { mediaKind } from '../detailsForm'
import { appliesTo } from '../mediaTypeSettings'
import { useStageTab } from '../useStageTab'
import { MEDIA_TAB_PREFIX, asMediaTab, mediaTabsFor, readMediaTab, resolveMediaTab, writeMediaTab, type MediaTab } from '../mediaTabs'
import { titleKind } from '../titleKind'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { useCanUpload } from '../../../hooks/useHolds'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { useStageDraft } from '../../../hooks/useStageDraft'
import {
  checkUploadFile,
  isUploadLimitProblem,
  isVideoFile,
  SOURCE_DRAFT_SHAPE,
  SOURCE_DRAFT_STAGE,
  sourceJobIds,
  UPLOAD_EXTENSIONS,
  UPLOAD_LIMIT_SETTINGS_HREF,
  SWITCHES_FROM_BURNED_IN,
  UPLOAD_SETS_VIDEO_ASIDE,
} from '../sourceForm'
import { useStage } from '../StageContext'
import { DetailsPanel } from './DetailsPanel'
import { FillInPanel } from './MetadataPanel'
import { JobPanel } from './JobPanel'
import { NovelChaptersPanel } from './NovelChaptersPanel'
import { SubtitleImport } from './SubtitleImport'
import { isSubtitleFileName } from './subtitleImportModel'
import { NovelFlow } from './NovelFlow'
import { NovelPanel } from './NovelPanel'
import { novelSections, stableNovelSections } from './novelFile'
import { useNovelPresence } from './novelFileEvents'
import TranscribeStage from './TranscribeStage'
import { MediaTabPanel } from './MediaTabPanel'
import { formatSizeMb } from '../../../labels'
import { TranscriptModePicker } from './SourceModes'
import { UrlDownload } from './UrlDownload'
import { mediaFileInputId, needsReplaceConfirm, replaceBoxId } from './stageBlockers'
import { narrationResumeNote } from './novelSetup'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'

// Nothing clears kept_media on its own, so say how much it holds wherever a
// replace or remove would add to it or might be expected to free it.
const keptMediaNote = (m: MediaStatus | null) =>
  m && m.kept_media_files > 0
    ? `Old copies kept in this title's folder: ${m.kept_media_files} file${m.kept_media_files === 1 ? '' : 's'}, ` +
      `${formatDecimalBytes(m.kept_media_bytes)}. Removing audio/video doesn't delete them; clear them in Library tools → Storage.`
    : null

export default function SourceStage() {
  const { dramaId, drama, onJobDone } = useStage()
  const [media, setMedia] = useState<MediaStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [replace, setReplace] = useState(false)
  // The server says the drama has audio/video even if the status we read did not.
  const [serverHasMedia, setServerHasMedia] = useState(false)
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  const transcribeRetry = useRef<(() => void) | null>(null)
  const [expectedSeconds, setExpectedSeconds] = useState<number | null>(null)
  const [reloads, setReloads] = useState(0)
  const pc = usePcOnly()
  const canUpload = useCanUpload()
  const [removeError, setRemoveError] = useState<unknown>(null)
  // "Upload a file" or "From a URL", remembered for this title.
  const { draft, save: saveDraft } = useStageDraft(dramaId, SOURCE_DRAFT_STAGE, SOURCE_DRAFT_SHAPE)
  const [from, setFrom] = useState<'file' | 'url'>(draft.from === 'url' ? 'url' : 'file')
  useEffect(() => {
    saveDraft({ from })
  }, [saveDraft, from])
  const fromUrl = from === 'url'

  // The sub-tab: a link's ?tab= first, Library's "Create and fill details with AI" (Fill in details is on Details), then the
  // one this title was last left on.
  const kindOfTitle = titleKind(drama.media_type, drama.content_mode)
  const linkedTab = () => {
    const r = parseRoute(window.location.hash)
    return r.name === 'drama' && r.id === dramaId ? (asMediaTab(r.tab) ?? undefined) : undefined
  }
  const [wantedTab, selectTab] = useStageTab<MediaTab>({
    dramaId,
    linked: linkedTab,
    initial: () => linkedTab() ?? (wantsAutofill(window.location.hash) ? 'details' : readMediaTab(browserStorage(), dramaId)),
    store: (next) => writeMediaTab(browserStorage(), dramaId, next),
  })
  const tab = resolveMediaTab(wantedTab, kindOfTitle)
  // Where the lines come from, once the picker on Add media has changed it.
  const [transcriptMode, setTranscriptMode] = useState<string | null>(null)
  // Which forms hold unsaved edits (Details and Credits share one draft), for the dot on the Details tab.
  const [unsaved, setUnsaved] = useState({ details: false, credits: false })
  // The button next to a "Still needed" line reveals the section that resolves it.
  const needMedia = () => {
    selectTab('add')
    setTimeout(() => {
      const el = document.getElementById(mediaFileInputId(dramaId))
      el?.scrollIntoView({ block: 'center' })
      el?.focus()
    }, 50)
  }

  useCancellableEffect((cancelled) => {
    getMediaStatus(dramaId).then(
      (m) => !cancelled() && setMedia(m),
      (e: unknown) => !cancelled() && setError(e),
    )
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

  // One drop box for both kinds of file: audio/video goes to Upload, a subtitle file to Import subtitle file.
  const [dragging, setDragging] = useState(false)
  const [droppedSubtitle, setDroppedSubtitle] = useState<File | null>(null)
  const [subtitlesOpen, setSubtitlesOpen] = useState(0)
  const mediaInput = useRef<HTMLInputElement | null>(null)
  const dropFile = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (!dropped) return
    // Import subtitle file is not offered without upload rights, so a dropped subtitle would vanish.
    if (isSubtitleFileName(dropped.name) && appliesTo(kindOfTitle, 'subtitle_import') && canUpload) {
      setDroppedSubtitle(dropped)
      setSubtitlesOpen((n) => n + 1)
      return
    }
    // The picker is disabled until the upload limit is known, and a drop must not skip its size check.
    if (!media) return
    if (mediaInput.current) mediaInput.current.files = e.dataTransfer.files
    pick(dropped)
  }

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
    if (!file || uploading || (mustConfirm && !replace)) return
    setUploading(true)
    uploadMedia(dramaId, file, confirmReplace).then(
      (r) => {
        setUploading(false)
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
        setUploading(false)
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
      (e: unknown) => {
        setRemoveError(e)
        // One file may be gone even though another could not be removed.
        setReloads((n) => n + 1)
        onJobDone()
      },
    )
  }

  const mediaSlot = (
    <>
      <div className="source-media-row">
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
        {media && (
          <p className="source-media-status" data-testid="media-status">
            <Badge tone={media.has_audio ? 'ok' : 'neutral'}>{media.has_audio ? 'Audio attached' : 'No audio'}</Badge>
            <Badge tone={media.has_source_video ? 'ok' : 'neutral'}>
              {media.has_source_video ? 'Source video attached' : 'No source video'}
            </Badge>
            <span className="muted">Limit {formatSizeMb(media.upload_max_mb)}</span>
          </p>
        )}
      </div>
      {fromUrl ? (
        media && (
          <UrlDownload
            key={dramaId}
            dramaId={dramaId}
            hasAudio={media.has_audio}
            hasSourceVideo={media.has_source_video}
            readsBurnedInSubtitles={media.reads_burned_in_subtitles}
            busy={busy}
            onStarted={(id) => {
              setUploaded(null)
              setJobId(id)
            }}
          />
        )
      ) : (
        <div
          className={`source-file source-drop${dragging ? ' is-dragging' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={dropFile}
        >
          <input
            ref={mediaInput}
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
            disabled={!file || busy || uploading || (mustConfirm && !replace)}
            onClick={upload}
          >
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
          {mustConfirm && (
            <label>
              <input type="checkbox" id={replaceBoxId(dramaId)} checked={replace} onChange={(e) => setReplace(e.target.checked)} />
              Replace the current audio/video (the old file is kept in this title's folder)
            </label>
          )}
          {mustConfirm && media?.has_source_video && !(file && isVideoFile(file.name)) && (
            <p className="muted" data-testid="replace-sets-video-aside">
              {UPLOAD_SETS_VIDEO_ASIDE}
              {media.reads_burned_in_subtitles && SWITCHES_FROM_BURNED_IN}
            </p>
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
      {(mustConfirm || hasMedia) && keptMediaNote(media) && (
        <p className="muted" data-testid="kept-media">{keptMediaNote(media)}</p>
      )}
      <ErrorBanner error={removeError} describe={{ pcOnly: true }} onDismiss={() => setRemoveError(null)} />
      {fileProblem && (
        <p className="error" role="alert">
          {fileProblem}
          {isUploadLimitProblem(fileProblem) && (
            <>
              {' '}
              <ButtonLink size="sm" href={UPLOAD_LIMIT_SETTINGS_HREF}>Raise the upload limit</ButtonLink>
            </>
          )}
        </p>
      )}
      {uploaded && <p role="status">{uploaded}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )

  // A novel is translated first, so its text leads; for audio the novel is the optional extra.
  // The title's kind decides, not just its media type: a narrated novel keeps whatever type it was made with.
  const kind = kindOfTitle === 'novel' ? 'novel' : mediaKind(drama.media_type)
  const novelPresence = useNovelPresence(dramaId, reloads)
  const presenceSections = novelSections(kind, drama.media_type, drama.content_mode, novelPresence)
  // A panel must not move under the viewer's hands: saving a raw novel from the optional row
  // leaves it there, and removing one from Tools leaves that panel (and its "Removed."
  // notice) there, until the page is reloaded or another title is opened.
  const [optionalFor, setOptionalFor] = useState<number | null>(null)
  const [fullFor, setFullFor] = useState<number | null>(null)
  const sections = stableNovelSections(kind, presenceSections, optionalFor === dramaId, fullFor === dramaId)
  // Record what is shown, not what was read, so a held layout never counts as the other one.
  if (novelPresence && sections === 'optional' && optionalFor !== dramaId) setOptionalFor(dramaId)
  if (novelPresence && sections === 'full' && fullFor !== dramaId) setFullFor(dramaId)
  const novel = sections !== 'hidden' && (appliesTo(kindOfTitle, 'novel_text') || appliesTo(kindOfTitle, 'chapter_ocr')) && (
    <div key="novel" className="source-group">
      {kind === 'novel' && <NovelFlow busy={busy} onJobStarted={setJobId} />}
      {kind === 'novel' && <NovelChaptersPanel />}
      <NovelPanel busy={busy} onOcrStarted={setJobId} reloadKey={reloads} kind={kind} primary={kind !== 'audio'} optional={sections === 'optional'} />
    </div>
  )
  const hasTranscribe = mediaTabsFor(kindOfTitle).some((t) => t.id === 'transcribe')
  const subtitles = appliesTo(kindOfTitle, 'subtitle_import') && canUpload && (
    <Section
      key="subtitles"
      storageKey="source.group.subtitles"
      defaultOpen={kind === 'audio'}
      title="Import subtitle file"
      summary="SRT, VTT, ASS or LRC"
      openSignal={subtitlesOpen}
    >
      <SubtitleImport dramaId={dramaId} busy={busy} onImported={onJobDone} onRealignStarted={setJobId} dropped={droppedSubtitle} />
    </Section>
  )
  // For a novel the audio is for dubbing the finished translation, so it waits behind a fold.
  const audio = !appliesTo(kindOfTitle, 'media_upload') ? null : kind === 'novel' ? (
    <Section
      key="audio"
      storageKey="source.group.novelAudio"
      defaultOpen={hasMedia}
      title="Audio (optional, for dubbing later)"
      summary={hasMedia ? 'attached' : 'none'}
    >
      {mediaSlot}
    </Section>
  ) : (
    mediaSlot
  )
  const addMedia = (
    <>
      {kind === 'audio' ? (
        <>
          {hasTranscribe && appliesTo(kindOfTitle, 'transcript_source') && <TranscriptModePicker onChanged={setTranscriptMode} />}
          {audio}
          {subtitles}
          {novel}
        </>
      ) : (
        <>
          {novel}
          {audio}
          {subtitles}
        </>
      )}
      <KeepOriginal />
    </>
  )

  // A live job shows on every tab; the finished run belongs to Transcribe.
  const showJob = jobId !== null || tab === 'transcribe'

  return (
    <div className="stage-source">
      <Tabs
        label="Media sections"
        idPrefix={MEDIA_TAB_PREFIX}
        tabs={mediaTabsFor(kindOfTitle).map((t) => ({ ...t, unsaved: t.id === 'details' && (unsaved.details || unsaved.credits) }))}
        active={tab}
        onSelect={selectTab}
      />
      <MediaTabPanel id="add" active={tab}>{addMedia}</MediaTabPanel>
      {hasTranscribe && (
        <TranscribeStage tab={tab} goTab={selectTab} transcriptMode={transcriptMode} media={media} file={file} confirmReplace={confirmReplace}
          replaceUnconfirmed={mustConfirm && !replace} onReplaceRefused={() => setServerHasMedia(true)} busy={busy}
          showRawNovel={sections === 'full'}
          retryRef={transcribeRetry}
          onJobStarted={(id, expected, sentFile) => {
            // Otherwise every later run would upload the same file again and keep another full copy.
            if (sentFile) {
              setFile(null)
              setReplace(false)
              setServerHasMedia(false)
              // An audio file is in place already, so the badges and notes are stale.
              setReloads((n) => n + 1)
            }
            setExpectedSeconds(expected ?? null)
            setJobId(id)
          }}
          onSendFailed={() => {
            const hadAudio = !!media?.has_audio
            getMediaStatus(dramaId).then((m) => {
              setMedia(m)
              // The server kept the picked file as the audio, so a later run must not upload it again.
              if (!hadAudio && m.has_audio) {
                setFile(null)
                setReplace(false)
                setServerHasMedia(false)
              }
            }, () => setReloads((n) => n + 1))
          }}
        />
      )}
      <DetailsPanel tab={tab} onUnsaved={setUnsaved} fillIn={<FillInPanel hasMedia={hasMedia} onNeedMedia={needMedia} />} />
      {showJob && (
        <JobPanel
          jobId={jobId}
          job={job}
          pollError={pollError}
          liveEta={!!jobId?.startsWith('transcribe_')}
          expectedSeconds={expectedSeconds}
          note={narrationResumeNote(job?.message)}
          lastRun={{
            dramaId,
            ids: sourceJobIds(dramaId),
            // Only a transcription has a form here to run again.
            retryFor: (j) => (j.job_id.startsWith('transcribe_') ? () => transcribeRetry.current?.() : null),
          }}
        />
      )}
    </div>
  )
}

const KEEP_ORIGINAL_HELP =
  "Storage cleanup never removes this title's source media, uploaded files, raw novel text or raw transcripts, " +
  'and an export includes them. They keep using disk space.'

/** The per-title "Keep original files" pin, saved through the metadata update like Edit details. */
function KeepOriginal() {
  const { dramaId, drama, refetchDrama } = useStage()
  const [pending, setPending] = useState<boolean | null>(null)
  const [error, setError] = useState<unknown>(null)
  const save = (next: boolean) => {
    setPending(next)
    updateDramaMetadata(dramaId, { keep_original: next }).then(
      () => {
        setError(null)
        refetchDrama()
      },
      (e: unknown) => {
        setPending(null)
        setError(e)
      },
    )
  }
  const saved = !!drama.keep_original
  // Show the choice while it saves; the next drama read is the truth again.
  if (pending !== null && pending === saved) setPending(null)
  return (
    <div className="setting-list" data-testid="keep-original">
      <Field label="Keep original files" help={KEEP_ORIGINAL_HELP}>
        <Toggle checked={pending ?? saved} disabled={pending !== null} onChange={save} />
      </Field>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
