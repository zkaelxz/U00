/*
 * UrlDownload: download a drama's audio/video from a link (R5,
 * POST /api/media/dramas/{id}/download-url, PC only). Used by the Workspace
 * Source stage ("From a URL") and by the Sources page's video preview card.
 *
 *   <UrlDownload dramaId={3} contentMode={drama.content_mode} hasAudio busy={busy} onStarted={setJobId} />
 *   <UrlDownload ... url={pastedUrl} />   // fixed link: no link field
 *
 * Remote viewers get one muted line instead ("PC only for now").
 */
import { useId, useState } from 'react'

import { ApiError } from '../../../api/client'
import { startUrlDownload } from '../../../api/sourcesImport'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { MAX_URL_LEN, defaultAudioOnly, downloadReason } from '../../sources/urlImportFormat'
import './urlDownload.css'

export const URL_PC_ONLY = 'Downloading from a link is PC only for now.'

type Props = {
  dramaId: number
  contentMode: string | null | undefined
  hasAudio: boolean
  // A fixed link (the Sources preview); without it a link field is shown.
  url?: string
  busy?: boolean
  onStarted: (jobId: string) => void
}

const needsConfirm = (e: unknown) =>
  e instanceof ApiError && e.status === 422 && (e.details as { reason?: unknown } | null)?.reason === 'confirm_replace_audio'

export function UrlDownload({ dramaId, contentMode, hasAudio, url: fixedUrl, busy, onStarted }: Props) {
  const pc = usePcOnly()
  const [typed, setTyped] = useState('')
  const [audioOnly, setAudioOnly] = useState(() => defaultAudioOnly(contentMode))
  const [replace, setReplace] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // The server says the drama has audio even if the status we read did not.
  const [serverHasAudio, setServerHasAudio] = useState(false)
  const reasonId = useId()

  if (pc === 'remote') return <p className="muted">{URL_PC_ONLY}</p>

  const url = fixedUrl ?? typed
  const audioThere = hasAudio || serverHasAudio
  const reason = downloadReason(url, audioThere, replace)

  const start = () => {
    if (reason || busy || starting) return
    setStarting(true)
    setError(null)
    startUrlDownload(dramaId, { url: url.trim(), audio_only: audioOnly, confirm_replace_audio: audioThere && replace }).then(
      (r) => {
        setStarting(false)
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
      <div className="url-download-checks">
        <label>
          <input type="checkbox" checked={audioOnly} onChange={(e) => setAudioOnly(e.target.checked)} />
          Audio only
        </label>
        {audioThere && (
          <label>
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            Replace the current audio
          </label>
        )}
      </div>
      <p className="muted">
        {audioOnly ? 'Keeps just the audio track.' : 'Keeps the video too and extracts its audio.'}
      </p>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={!!reason || busy || starting}
          aria-describedby={reason ? reasonId : undefined}
          onClick={start}
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
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
    </div>
  )
}
