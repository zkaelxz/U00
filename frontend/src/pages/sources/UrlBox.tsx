/*
 * UrlBox: paste a link, preview what it is (S-5, R1: a paced job,
 * sources_url_preview), then act on it:
 *
 *   series / chapter link  "Open series" (the chapter is ticked)
 *   novel page             pick a drama, "Import text" (R2, sourceimport_<drama>)
 *   video                  pick a drama, download it (R5, PC only, urlmedia_<drama>)
 *   comic / unknown        a short explanation
 *
 * A browser check shows a handoff card ("Open in your browser", "Try again");
 * nothing retries by itself. The link is kept in memory only: a preview
 * found on load (an earlier run) shows, but importing needs the link again.
 */
import { useEffect, useState } from 'react'

import { ApiError } from '../../api/client'
import { sourceImportJobId, startUrlImport, startUrlPreview, URL_PREVIEW_JOB_ID } from '../../api/sourcesImport'
import { getMediaStatus } from '../../api/workspace'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { useJob, useJobRun } from '../../hooks/useJob'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { OpenSeries } from '../../types/sources'
import type { UrlImportResult, UrlPreview } from '../../types/sourcesImport'
import type { MediaStatus } from '../../types/workspace'
import { JobPanel } from '../workspace/stages/JobPanel'
import { URL_PC_ONLY, UrlDownload } from '../workspace/stages/UrlDownload'
import { DramaPicker } from './DramaPicker'
import { useDramaList } from './useDramaList'
import { describeSourceError, percent, safeHref } from './sourcesFormat'
import {
  MAX_URL_LEN, PREVIEW_NOTES, checkUrl, contentTypeLabel, dramaLabel, novelDramas, previewAction, previewFacts,
  urlImportText, videoDramas,
} from './urlImportFormat'
import { useSourcesJob } from './useSourcesJob'

type Props = {
  // Source name -> display name.
  display: (name: string) => string
  onOpenSeries: (s: OpenSeries, chapterId: string | null) => void
}

const isHandoff = (e: unknown) =>
  e instanceof ApiError && e.status === 409 && !!(e.details as { handoff?: unknown } | null)?.handoff

export function UrlBox({ display, onOpenSeries }: Props) {
  const [text, setText] = useState('')
  // The link the shown preview is for (null: a preview found on load).
  const [previewed, setPreviewed] = useState<string | null>(null)
  const job = useSourcesJob<UrlPreview>(URL_PREVIEW_JOB_ID)
  const running = job.status === 'running'
  const reason = checkUrl(text)

  function submit(e?: React.FormEvent) {
    e?.preventDefault()
    if (reason || running) return
    const url = text.trim()
    setPreviewed(url)
    job.start(() => startUrlPreview(url))
  }

  const retry = () => {
    if (!previewed || running) return
    job.start(() => startUrlPreview(previewed))
  }

  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const preview = job.status === 'done' && job.result?.kind === 'url_preview' ? job.result : null

  return (
    <div className="sources-url">
      <form className="sources-search-row sources-url-row" onSubmit={submit}>
        <Field label="Paste a link" help="A series, chapter, novel or video page. Baihe checks what it is first; nothing is imported yet.">
          <input
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_URL_LEN}
            placeholder="https://…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </Field>
        <button type="submit" disabled={!!reason || running}>
          Preview
        </button>
      </form>
      {reason && text.trim() !== '' && <p className="muted sources-reason">{reason}</p>}

      <div aria-live="polite" className="sources-running">
        {running && (
          <div className="sources-url-running">
            <progress value={job.progress ?? undefined} max={1} aria-label="Preview progress" />
            <p>
              {job.message || 'Checking the link…'}
              {percent(job.progress)} ·{' '}
              <button type="button" className="link" onClick={() => void job.cancel()}>
                Cancel
              </button>
            </p>
          </div>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      {failed && isHandoff(failed) ? (
        <HandoffCard error={failed} onRetry={previewed ? retry : undefined} />
      ) : (
        failed && (
          <p className="warn source-error" role="alert">
            {describeSourceError(failed, 'The site').text}
          </p>
        )
      )}
      {preview && <PreviewCard key={previewed ?? ''} preview={preview} url={previewed} display={display} onOpenSeries={onOpenSeries} />}
    </div>
  )
}

function HandoffCard({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const copy = describeSourceError(error, 'The site')
  return (
    <div className="sources-card sources-handoff" role="alert">
      <p>{copy.text}</p>
      <p className="muted">Open the page in your own browser, get past the check there, then try again.</p>
      <div className="actions">
        {copy.openUrl && (
          <a href={copy.openUrl} target="_blank" rel="noopener noreferrer">
            Open in your browser ↗
          </a>
        )}
        {onRetry && (
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  )
}

function PreviewCard({ preview: p, url, display, onOpenSeries }: {
  preview: UrlPreview
  url: string | null
  display: (name: string) => string
  onOpenSeries: Props['onOpenSeries']
}) {
  const action = previewAction(p)
  const facts = previewFacts(p)
  const link = safeHref(p.display_url)
  const title = p.title?.trim() || p.chapter?.trim() || 'Untitled page'
  const needLink = (action === 'novel' || action === 'video') && !url
  return (
    <article className="sources-card sources-preview" aria-label="Link preview" data-testid="url-preview">
      <div className="sources-series-head">
        <h3>{title}</h3>
        <span className="badge">{contentTypeLabel(p.content_type)}</span>
      </div>
      {facts.length > 0 && <p className="sources-meta">{facts.join(' · ')}</p>}
      {p.notes.length > 0 && (
        <ul className="sources-notes">
          {p.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      {link && <p className="muted sources-link">{link}</p>}

      {action === 'series' && p.adapter && p.series_id && (
        <div className="actions">
          <button
            type="button"
            className="primary"
            onClick={() => onOpenSeries({ source: p.adapter!, series_id: p.series_id!, title: p.title ?? '' }, p.chapter_id)}
          >
            Open series
          </button>
          <span className="muted">
            On {display(p.adapter)}
            {p.chapter_id ? '; this chapter will be ticked.' : '.'}
          </span>
        </div>
      )}
      {needLink && <p className="muted">Paste the link again and press Preview to import it.</p>}
      {action === 'novel' && url && <NovelImport url={url} title={title} language={p.language} />}
      {action === 'video' && url && <VideoImport url={url} />}
      {(action === 'comic' || action === 'unknown') && <p className="muted">{PREVIEW_NOTES[action]}</p>}
    </article>
  )
}

function NovelImport({ url, title, language }: { url: string; title: string; language: string | null }) {
  const dramas = useDramaList()
  const [dramaId, setDramaId] = useState<number | null>(null)
  const job = useSourcesJob<UrlImportResult>(dramaId ? sourceImportJobId(dramaId) : null)
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'url_import' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null

  const start = () => {
    if (!dramaId || running) return
    job.start(() => startUrlImport(url, dramaId))
  }

  return (
    <div className="sources-import" role="group" aria-label="Import text">
      <DramaPicker
        dramas={dramas.items ? novelDramas(dramas.items) : null}
        value={dramaId}
        onChange={setDramaId}
        disabled={running}
        newDrama={{ title, language, comic: false }}
        onCreated={dramas.add}
        help="The chapter text is added to the end of the drama’s novel text."
      />
      <ErrorBanner error={dramas.error} />
      <div className="actions">
        <button type="button" className="primary" disabled={!dramaId || running} onClick={start}>
          {running ? 'Importing…' : 'Import text'}
        </button>
        {running && (
          <button type="button" onClick={() => void job.cancel()}>
            Cancel
          </button>
        )}
        {!dramaId && !running && <span className="muted">Still needed: a drama to import into.</span>}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      <div aria-live="polite">
        {running && <p>{job.message || 'Importing…'}{percent(job.progress)}</p>}
        {failed && <p className="warn" role="alert">{describeSourceError(failed, 'The site').text}</p>}
        {result && (
          <p className={result.needs_review ? 'warn' : undefined} data-testid="url-import-result">
            {urlImportText(result)}{' '}
            {dramaId && <a href={`#/drama/${dramaId}/source`}>Open workspace</a>}
          </p>
        )}
      </div>
    </div>
  )
}

function VideoImport({ url }: { url: string }) {
  const dramas = useDramaList()
  const [dramaId, setDramaId] = useState<number | null>(null)
  const [media, setMedia] = useState<MediaStatus | null>(null)
  const [mediaError, setMediaError] = useState<unknown>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const { job, done, error: pollError } = useJob(jobId, { runKey })
  const busy = jobId !== null && !done && !pollError
  const list = dramas.items ? videoDramas(dramas.items) : null
  const drama = list?.find((d) => d.id === dramaId) ?? null
  const remote = usePcOnly() === 'remote'

  useEffect(() => {
    if (!dramaId) return
    let live = true
    getMediaStatus(dramaId).then(
      (m) => live && setMedia(m),
      (e: unknown) => live && setMediaError(e),
    )
    return () => {
      live = false
    }
  }, [dramaId])

  if (remote) return <p className="muted">{URL_PC_ONLY}</p>

  const choose = (id: number | null) => {
    setMedia(null)
    setMediaError(null)
    setDramaId(id)
  }

  return (
    <div className="sources-import" role="group" aria-label="Download video">
      <DramaPicker
        dramas={list}
        value={dramaId}
        onChange={choose}
        disabled={busy}
        help="Audio drama or streamer VOD dramas only."
      />
      <ErrorBanner error={dramas.error ?? mediaError} />
      {drama && media && media.drama_id === drama.id && (
        <UrlDownload
          key={drama.id}
          dramaId={drama.id}
          contentMode={drama.content_mode}
          hasAudio={media.has_audio}
          url={url}
          busy={busy}
          onStarted={setJobId}
        />
      )}
      {jobId && (
        <>
          <JobPanel job={job} pollError={pollError} />
          {drama && done && <a href={`#/drama/${drama.id}/source`}>Open {dramaLabel(drama)} in the workspace</a>}
        </>
      )}
    </div>
  )
}
