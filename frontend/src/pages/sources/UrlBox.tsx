/*
 * UrlBox: paste a link, preview what it is (S-5, R1: a paced job,
 * sources_url_preview), then act on it:
 *
 *   series / chapter link  "Open series" (the chapter is ticked)
 *   novel page             pick a drama, "Import text" (R2, NovelUrlImport)
 *   comic page             pick a drama, "Import pages" (SO06, ComicUrlImport)
 *   video                  pick a drama, download it (R5, PC only, urlmedia_<drama>);
 *                          no adapter: "Identify media" picks a resource (SO08)
 *   unknown                a short explanation
 *
 * A browser check shows a handoff card ("Open in your browser", "Try again",
 * or paste the page source once past the check: PastedSource, SO03, whose
 * preview and novel import read the paste instead of the site); nothing
 * retries by itself. "Will this site work?" (SiteCheck, SO02) checks the
 * typed link once without importing. The link is kept in memory only: a preview
 * found on load (an earlier run) shows, but importing needs the link again.
 */
import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { startUrlPreview, URL_PREVIEW_JOB_ID } from '../../api/sourcesImport'
import { getMediaStatus } from '../../api/workspace'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useJob, useJobRun } from '../../hooks/useJob'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { OpenSeries } from '../../types/sources'
import type { UrlPreview } from '../../types/sourcesImport'
import type { PastedPreview } from '../../types/sourcesTools'
import type { MediaStatus } from '../../types/workspace'
import { JobPanel } from '../workspace/stages/JobPanel'
import { URL_PC_ONLY, UrlDownload } from '../workspace/stages/UrlDownload'
import { ComicUrlImport } from './ComicUrlImport'
import { DramaPicker } from './DramaPicker'
import { IdentifyMedia } from './IdentifyMedia'
import { NovelUrlImport } from './NovelUrlImport'
import { PastedSource } from './PastedSource'
import { SiteCheck } from './SiteCheck'
import { useDramaList } from './useDramaList'
import { describeSourceError, percent } from './sourcesFormat'
import { safeHref } from './sourcesSeries'
import {
  MAX_URL_LEN, PASTED_COMIC_NOTE, PREVIEW_NOTES, checkUrl, contentTypeLabel, dramaLabel, previewAction, previewFacts, videoDramas,
} from './urlImportFormat'
import { useSourcesJob } from './useSourcesJob'

type Props = {
  // Source name -> display name.
  display: (name: string) => string
  onOpenSeries: (s: OpenSeries, chapterId: string | null) => void
  // A link handed over by the web-search fallback: fills the box; the user
  // still presses Preview.
  handoff?: { url: string; n: number } | null
}

const isHandoff = (e: unknown) =>
  e instanceof ApiError && e.status === 409 && !!(e.details as { handoff?: unknown } | null)?.handoff

export function UrlBox({ display, onOpenSeries, handoff }: Props) {
  const [text, setText] = useState('')
  const input = useRef<HTMLInputElement>(null)
  // Each hand-off is a new object, so only a new one refills the box.
  const [takenHandoff, setTakenHandoff] = useState<Props['handoff']>(null)
  if (handoff && handoff !== takenHandoff) {
    setTakenHandoff(handoff)
    setText(handoff.url.slice(0, MAX_URL_LEN))
  }
  useEffect(() => {
    if (handoff) requestAnimationFrame(() => input.current?.focus())
  }, [handoff])
  // The link the shown preview is for (null: a preview found on load).
  const [previewed, setPreviewed] = useState<string | null>(null)
  // SO03: a preview read from page source pasted after a verification page.
  const [pasted, setPasted] = useState<{ url: string; html: string; preview: PastedPreview } | null>(null)
  // No reattach on 409: the running preview may be another tab's link, and
  // the card would then show that link while importing acts on `previewed`.
  const job = useSourcesJob<UrlPreview>(URL_PREVIEW_JOB_ID, { reattachOn409: false })
  const running = job.status === 'running'
  const reason = checkUrl(text)

  function submit(e?: React.FormEvent) {
    e?.preventDefault()
    if (reason || running) return
    const url = text.trim()
    setPreviewed(url)
    setPasted(null)
    job.start(() => startUrlPreview(url))
  }

  const retry = () => {
    if (!previewed || running) return
    setPasted(null)
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
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </Field>
        {/* The card's one primary, until a preview's own action takes over. */}
        <button type="submit" className={buttonClass(preview ? 'secondary' : 'primary')} disabled={!!reason || running}>
          Preview
        </button>
      </form>
      {reason && text.trim() !== '' && <p className="muted sources-reason">{reason}</p>}
      <SiteCheck url={text.trim()} disabled={!!reason} />

      <div aria-live="polite" className="sources-running">
        {running && (
          <div className="sources-url-running">
            <progress value={job.progress ?? undefined} max={1} aria-label="Preview progress" />
            <p>
              {job.message || 'Checking the link…'}
              {percent(job.progress)} ·{' '}
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => void job.cancel()}>
                Cancel
              </button>
            </p>
          </div>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      {pasted ? (
        <PreviewCard
          key={`pasted:${pasted.url}`}
          preview={pasted.preview}
          url={pasted.url}
          html={pasted.html}
          display={display}
          onOpenSeries={onOpenSeries}
        />
      ) : failed && isHandoff(failed) ? (
        <HandoffCard
          error={failed}
          onRetry={previewed ? retry : undefined}
          onPasted={previewed ? (html, p) => setPasted({ url: previewed, html, preview: p }) : undefined}
          url={previewed}
        />
      ) : (
        failed && (
          <p className="warn source-error" role="alert">
            {describeSourceError(failed, 'The site').text}
          </p>
        )
      )}
      {preview && !pasted && (
        <PreviewCard key={previewed ?? ''} preview={preview} url={previewed} display={display} onOpenSeries={onOpenSeries} />
      )}
    </div>
  )
}

function HandoffCard({ error, onRetry, onPasted, url }: {
  error: unknown
  onRetry?: () => void
  onPasted?: (html: string, p: PastedPreview) => void
  url: string | null
}) {
  const copy = describeSourceError(error, 'The site')
  return (
    <div className="sources-card sources-handoff" role="alert">
      <p>{copy.text}</p>
      <p className="muted">Open the page in your own browser, get past the check there, then try again.</p>
      <div className="actions">
        {copy.openUrl && (
          <ButtonLink href={copy.openUrl} target="_blank" rel="noopener noreferrer">
            Open in your browser ↗
          </ButtonLink>
        )}
        {onRetry && (
          <button type="button" className={buttonClass('secondary')} onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
      {onPasted && url && <PastedSource url={url} onPreview={onPasted} />}
    </div>
  )
}

function PreviewCard({ preview: p, url, html = null, display, onOpenSeries }: {
  preview: UrlPreview
  url: string | null
  // SO03: the pasted page source this preview was read from.
  html?: string | null
  display: (name: string) => string
  onOpenSeries: Props['onOpenSeries']
}) {
  const action = previewAction(p)
  const facts = previewFacts(p)
  const link = safeHref(p.display_url)
  const title = p.title?.trim() || p.chapter?.trim() || 'Untitled page'
  const needLink = (action === 'novel' || action === 'video' || action === 'comic') && !url
  return (
    <article className="sources-card sources-preview" aria-label="Link preview" data-testid="url-preview">
      <div className="sources-series-head">
        <h3>{title}</h3>
        <Badge tone="info">{contentTypeLabel(p.content_type)}</Badge>
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
      {html && <p className="muted">Read from the page source you pasted.</p>}

      {action === 'series' && p.adapter && p.series_id && (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('primary')}
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
      {action === 'novel' && url && <NovelUrlImport url={url} html={html} title={title} language={p.language} />}
      {action === 'video' && url && <VideoImport url={url} html={html} identify={!p.adapter} />}
      {action === 'comic' && url && !html && <ComicUrlImport url={url} title={title} language={p.language} />}
      {action === 'comic' && html && <p className="muted">{PASTED_COMIC_NOTE}</p>}
      {action === 'unknown' && <p className="muted">{PREVIEW_NOTES[action]}</p>}
    </article>
  )
}

function VideoImport({ url, html, identify }: { url: string; html: string | null; identify: boolean }) {
  const dramas = useDramaList()
  // SO08: a resource picked on the page, downloaded instead of the page link.
  const [picked, setPicked] = useState<string | null>(null)
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
    // The shown download belongs to the drama it was started for.
    setJobId(null)
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
      {identify && <IdentifyMedia url={url} html={html} disabled={busy} onPick={setPicked} />}
      {drama && media && media.drama_id === drama.id && (
        <UrlDownload
          key={drama.id}
          dramaId={drama.id}
          contentMode={drama.content_mode}
          hasAudio={media.has_audio}
          hasSourceVideo={media.has_source_video}
          readsBurnedInSubtitles={media.reads_burned_in_subtitles}
          url={picked ?? url}
          busy={busy}
          onStarted={setJobId}
        />
      )}
      {jobId && (
        <>
          <JobPanel job={job} pollError={pollError} />
          {drama && done && (
            <ButtonLink href={`#/drama/${drama.id}/source`}>Open {dramaLabel(drama)} in the workspace</ButtonLink>
          )}
        </>
      )}
    </div>
  )
}
