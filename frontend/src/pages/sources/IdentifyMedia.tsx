/*
 * IdentifyMedia (SO08): on a video page nothing else recognizes, list the
 * video/audio/subtitle resources the page exposes (job sources_url_identify:
 * one paced fetch on the PC, or the pasted page source; no AI) and let the
 * person pick which one the video download fetches. The result shows
 * scheme+host+path only; the picked resource's full address comes from a
 * PC-only lookup, like the download itself. Protected streams are named,
 * never decrypted.
 */
import { useId, useState } from 'react'

import { getIdentifiedResource, IDENTIFY_JOB_ID, startIdentifyMedia } from '../../api/sourcesTools'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { MediaIdentify } from '../../types/sourcesTools'
import { describeSourceError, percent } from './sourcesFormat'
import { playableResources, resourceLabel } from './sourcesToolsFormat'
import { useSourcesJob } from './useSourcesJob'
import './sources-tools.css'

type Props = {
  url: string
  // Pasted page source (SO03): identify from it instead of fetching.
  html: string | null
  disabled?: boolean
  // The full address to download, or null for the page link itself.
  onPick: (resourceUrl: string | null) => void
}

export function IdentifyMedia({ url, html, disabled, onPick }: Props) {
  const job = useSourcesJob<MediaIdentify>(IDENTIFY_JOB_ID, { reattachOn409: false })
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'media_identify' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const [chosen, setChosen] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const name = useId()

  function identify() {
    setChosen(null)
    setError(null)
    onPick(null)
    job.start(() => startIdentifyMedia(url, html))
  }

  async function choose(index: number | null) {
    setError(null)
    setChosen(index)
    if (index === null || !result) {
      onPick(null)
      return
    }
    setBusy(true)
    try {
      const r = await getIdentifiedResource(result.run_id, index)
      onPick(r.resource_url)
    } catch (e) {
      setChosen(null)
      onPick(null)
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const playable = result ? playableResources(result.resources) : []
  const subs = result ? result.resources.filter((r) => r.kind === 'subtitle') : []

  return (
    <div className="sources-import" role="group" aria-label="Identify media">
      <div className="actions">
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={disabled || running} aria-busy={running} onClick={identify}>
          {running ? 'Looking…' : 'Identify media on this page'}
        </button>
        {running && (
          <span className="muted" role="status">
            {job.message || 'Looking for media…'}
            {percent(job.progress)}
          </span>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      {failed && (
        <p className="warn" role="alert">
          {describeSourceError(failed, 'The site').text}
        </p>
      )}
      {result && (
        <div aria-live="polite" data-testid="media-identify">
          {result.protection.map((p) => (
            <p key={p} className="warn">
              {p} detected on this page; a protected stream won't be decrypted.
            </p>
          ))}
          {playable.length === 0 ? (
            <p className="muted">{result.reason || 'No video or audio was found on this page.'}</p>
          ) : (
            <fieldset className="sources-media" disabled={busy || disabled}>
              <legend>Resource to import</legend>
              {result.reason && <p className="muted">{result.reason}</p>}
              <label>
                <input type="radio" name={name} checked={chosen === null} onChange={() => void choose(null)} />
                <span>The page link itself</span>
              </label>
              {playable.map((r) => (
                <label key={r.index}>
                  <input type="radio" name={name} checked={chosen === r.index} onChange={() => void choose(r.index)} />
                  <span>
                    {resourceLabel(r)}
                    <span className="muted sources-link">{r.display_url}</span>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {subs.map((r) => (
            <p key={r.index} className="muted sources-link">
              Subtitle ({r.language || '?'}): {r.display_url}
            </p>
          ))}
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </div>
  )
}
