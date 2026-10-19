/*
 * UrlDownload: download a drama's audio/video from a link (R5,
 * POST /api/media/dramas/{id}/download-url, PC only). Used by the Workspace
 * Source stage ("From a URL") and by the Sources page's video preview card.
 *
 *   <UrlDownload dramaId={3} hasAudio hasSourceVideo busy={busy} onStarted={setJobId} />
 *   <UrlDownload ... url={pastedUrl} />   // fixed link: no link field
 *
 * Remote viewers get one muted line instead ("PC only for now").
 */
import { useEffect, useId, useRef, useState } from 'react'

import { hasStatus } from '../../../api/client'
import { startUrlDownload } from '../../../api/sourcesImport'
import type { UrlDownloadRequest } from '../../../types/sourcesImport'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import { useJob } from '../../../hooks/useJob'
import { refreshReadiness, useReadiness } from '../../../hooks/useReadiness'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { useStageDraft } from '../../../hooks/useStageDraft'
import { NeedsInstall } from '../../readiness/NeedsInstall'
import { urlDownloadNeeds } from '../../readiness/needs'
import {
  EXACT_CUTS_HELP, MAX_URL_LEN, PART_CLIP_NOTE, QUEUE_NOTE, downloadReason, partProblem,
} from '../../sources/urlImportFormat'
import { SWITCHES_FROM_BURNED_IN, URL_DRAFT_SHAPE, URL_DRAFT_STAGE, URL_SETS_VIDEO_ASIDE, isDirectAudioUrl, isDirectMediaUrl } from '../sourceForm'
import './urlDownload.css'

type Props = {
  dramaId: number
  hasAudio: boolean
  hasSourceVideo?: boolean
  readsBurnedInSubtitles?: boolean
  // A fixed link (the Sources preview); without it a link field is shown.
  url?: string
  busy?: boolean
  onStarted: (jobId: string) => void
}

const needsConfirm = (e: unknown) =>
  hasStatus(e, 422) && (e.details as { reason?: unknown } | null)?.reason === 'confirm_replace_audio'

export function UrlDownload({ dramaId, hasAudio, hasSourceVideo, readsBurnedInSubtitles, url: fixedUrl, busy, onStarted }: Props) {
  const pc = usePcOnly()
  const { readiness } = useReadiness()
  // The link and Audio only as last left for this title; Replace is never kept.
  const { draft, save: saveDraft } = useStageDraft(dramaId, URL_DRAFT_STAGE, URL_DRAFT_SHAPE)
  const [typed, setTyped] = useState(draft.url ?? '')
  const [audioOnly, setAudioOnly] = useState(() => draft.audioOnly ?? isDirectAudioUrl(fixedUrl ?? draft.url ?? ''))
  useEffect(() => {
    saveDraft({ url: typed, audioOnly })
  }, [saveDraft, typed, audioOnly])
  const [replace, setReplace] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // The server says the drama has audio even if the status we read did not.
  const [serverHasAudio, setServerHasAudio] = useState(false)
  const reasonId = useId()
  // "Only this part": kept for this visit only, like Replace.
  const [partStart, setPartStart] = useState('')
  const [partEnd, setPartEnd] = useState('')
  const [exactCuts, setExactCuts] = useState(false)
  const partStartRef = useRef<HTMLInputElement>(null)
  // The job this form started, to offer the choices after a "longer than 6 hours" refusal.
  const [started, setStarted] = useState<{ id: string; key: number } | null>(null)
  const { job } = useJob(started?.id ?? null, { runKey: started?.key })

  if (pc === 'remote') return null

  const url = fixedUrl ?? typed
  const audioThere = hasAudio || serverHasAudio
  const partReason = partProblem(partStart, partEnd)
  // Unknown readiness needs nothing: the server still refuses what cannot run.
  const needs = urlDownloadNeeds(readiness, isDirectMediaUrl(url))
  const reason = downloadReason(url, audioThere, replace) ?? partReason ?? (needs.length ? 'Still needed: set up what is listed below.' : null)
  const hasPart = partStart.trim() !== '' || partEnd.trim() !== ''
  // The server's own words for why it refused (services/url_media_service.py failed_reason).
  const refusedFor = job?.status === 'error' ? (job.result as { failed_reason?: string } | null | undefined)?.failed_reason : undefined
  const audioOnlyResult = audioOnly || isDirectAudioUrl(url)

  const start = (anyway = false) => {
    if (reason || busy || starting) return
    setStarting(true)
    setError(null)
    const body: UrlDownloadRequest = { url: url.trim(), audio_only: audioOnly, confirm_replace_audio: audioThere && replace }
    if (anyway) body.allow_long = true
    if (hasPart && !isDirectAudioUrl(url)) {
      if (partStart.trim()) body.part_start = partStart.trim()
      if (partEnd.trim()) body.part_end = partEnd.trim()
      if (exactCuts) body.exact_cuts = true
    }
    startUrlDownload(dramaId, body).then(
      (r) => {
        setStarting(false)
        setStarted((prev) => ({ id: r.job_id, key: (prev?.key ?? 0) + 1 }))
        onStarted(r.job_id)
      },
      (e: unknown) => {
        setStarting(false)
        if (needsConfirm(e)) setServerHasAudio(true)
        setError(e)
      },
    )
  }

  return (
    <div className="url-download" data-testid="url-download">
      {fixedUrl === undefined && (
        <Field label="Video or audio link" help="A video page from a supported site, or a direct link to an audio/video file. Playlists and live streams are refused.">
          <input
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_URL_LEN}
            placeholder="https://…"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && start()}
          />
        </Field>
      )}
      <div className="setting-list">
        <Field label="Audio only">
          <Toggle checked={audioOnly} onChange={setAudioOnly} />
        </Field>
      </div>
      {!isDirectAudioUrl(url) && (
        <fieldset className="url-download-part">
          <legend>Only this part (optional)</legend>
          <div className="url-download-part-times">
            <label>
              From
              <input
                ref={partStartRef}
                type="text"
                inputMode="numeric"
                autoComplete="off"
                placeholder="0:00:00"
                aria-label="Part start (h:mm:ss)"
                value={partStart}
                onChange={(e) => setPartStart(e.target.value)}
              />
            </label>
            <label>
              To
              <input
                type="text"
                inputMode="numeric"
                autoComplete="off"
                placeholder="end"
                aria-label="Part end (h:mm:ss)"
                value={partEnd}
                onChange={(e) => setPartEnd(e.target.value)}
              />
            </label>
          </div>
          {hasPart && (
            <>
              <label>
                <input type="checkbox" checked={exactCuts} onChange={(e) => setExactCuts(e.target.checked)} />
                Exact cuts
              </label>
              <p className="muted">{EXACT_CUTS_HELP}</p>
              <p className="muted" data-testid="url-part-offset">
                {PART_CLIP_NOTE}
              </p>
            </>
          )}
        </fieldset>
      )}
      {audioThere && (
        <div className="url-download-checks">
          <label>
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            Replace the current audio
          </label>
          {audioOnlyResult && hasSourceVideo && (
            <p className="muted" data-testid="url-sets-video-aside">
              {URL_SETS_VIDEO_ASIDE}
              {readsBurnedInSubtitles && SWITCHES_FROM_BURNED_IN}
            </p>
          )}
        </div>
      )}
      <p className="muted">
        {audioOnlyResult ? 'Keeps just the audio track.' : 'Keeps the video too and extracts its audio.'}
      </p>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={!!reason || busy || starting}
          aria-describedby={reason ? reasonId : undefined}
          onClick={() => start()}
        >
          {starting ? 'Starting…' : 'Download'}
        </button>
        {reason && url.trim() !== '' && (
          <span id={reasonId} className="muted">
            {reason}
          </span>
        )}
        {busy && <span className="muted">Wait for the running job to finish.</span>}
      </div>
      {needs.map((n) => (
        <NeedsInstall key={n.id} need={n} onInstalled={() => void refreshReadiness()} />
      ))}
      <p className="muted">{QUEUE_NOTE}</p>
      {refusedFor === 'too_long' && !busy && (
        <div className="url-download-long" data-testid="url-download-long">
          <p>Download only part of it, or all of it anyway (up to 12 hours; free disk space is checked first).</p>
          <div className="actions">
            <button type="button" className="secondary" onClick={() => partStartRef.current?.focus()}>
              Download only part
            </button>
            <button type="button" disabled={!!reason || starting} onClick={() => start(true)}>
              Download anyway
            </button>
          </div>
        </div>
      )}
      {refusedFor === 'too_long_max' && !busy && (
        <p className="muted">Even with Download anyway the limit is 12 hours: choose a part above.</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
    </div>
  )
}
