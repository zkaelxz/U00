// The Watch page's "Cast to TV": sends the title's English-subtitled copy to a Chromecast or smart TV
// through the browser's own cast picker, by a short-lived link the TV can fetch without signing in.
import { useCallback, useEffect, useRef, useState } from 'react'

import { endShareLinks, getCastStatus, makeCastLink, makeShareLink } from '../../api/cast'
import { artifactUrl } from '../../api/client'
import { startBurnedVideo } from '../../api/export'
import { buttonClass } from '../../components/uiClasses'
import type { CastStatus, ShareDays } from '../../types/cast'
import { SHARE_CHOICES, castLabel, castUrl, reachableByTv, shareNote } from './castLogic'

const POLL_MS = 5000

type Remote = { prompt: () => Promise<void> }

function remoteOf(video: HTMLVideoElement): Remote | null {
  return (video as HTMLVideoElement & { remote?: Remote }).remote ?? null
}

export function CastCard({ id, canMake, canCast }: { id: number; canMake: boolean; canCast: boolean }) {
  const supported = typeof document !== 'undefined' && 'remote' in HTMLVideoElement.prototype
  const [state, setState] = useState<CastStatus['state'] | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [shareDays, setShareDays] = useState<ShareDays>(1)
  const [shared, setShared] = useState<{ url: string; days: ShareDays } | null>(null)
  const holder = useRef<HTMLVideoElement | null>(null)
  const show = canCast
  const reachable = reachableByTv(window.location.hostname)
  const castable = supported && reachable

  const look = useCallback(async () => {
    try {
      setState((await getCastStatus(id)).state)
    } catch {
      setState(null)
    }
  }, [id])

  useEffect(() => {
    if (!show) return
    let off = false
    getCastStatus(id).then((got) => !off && setState(got.state), () => undefined)
    return () => {
      off = true
    }
  }, [id, show])

  useEffect(() => {
    if (state !== 'working') return
    const timer = window.setInterval(() => void look(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [state, look])

  if (!show) return null

  const make = async () => {
    setBusy(true)
    setNote('')
    try {
      await startBurnedVideo(id, { field: 'en', preset: 'Clean', per_speaker_colors: true, include_notes: false, notes_as_separate_line: false })
      setState('working')
    } catch {
      setNote('The subtitled copy could not be started.')
    } finally {
      setBusy(false)
    }
  }

  const cast = async () => {
    setBusy(true)
    setNote('')
    try {
      const link = await makeCastLink(id)
      const video = holder.current
      const remote = video ? remoteOf(video) : null
      if (!video || !remote) throw new Error('no remote')
      video.src = castUrl(window.location.origin, link.path)
      await remote.prompt()
    } catch (e) {
      // Closing the picker without choosing a TV is a refusal, not a failure.
      if (!(e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'NotFoundError' || e.name === 'AbortError'))) {
        setNote('Casting did not start. Check that the TV is on the same network and try again.')
      }
    } finally {
      setBusy(false)
    }
  }

  const share = async () => {
    setBusy(true)
    setNote('')
    try {
      const link = await makeShareLink(id, shareDays)
      setShared({ url: castUrl(window.location.origin, link.path), days: shareDays })
    } catch {
      setNote('The link could not be made.')
    } finally {
      setBusy(false)
    }
  }

  const copyShared = async () => {
    try {
      await navigator.clipboard.writeText(shared?.url ?? '')
      setNote('Link copied.')
    } catch {
      setNote('Copy the link from the box.')
    }
  }

  const endShared = async () => {
    try {
      await endShareLinks(id)
      setShared(null)
      setNote('Shared links ended.')
    } catch {
      setNote('The links could not be ended.')
    }
  }

  return (
    <section className="card watch-cast" aria-label="Cast and save for offline">
      <p><strong>Watch elsewhere</strong></p>
      <p role="status">{note || castLabel(state)}</p>
      <div className="watch-card-actions">
        {state === 'ready' && castable && (
          <button type="button" className={buttonClass('primary')} disabled={busy} onClick={() => void cast()}>
            Cast to TV
          </button>
        )}
        {(state === 'ready' || state === 'unsupported') && (
          // The copy has the English subtitles burned in, so any player shows them with no signal.
          <a className={buttonClass()} href={artifactUrl(id, 'video')} download>Save to phone</a>
        )}
        {(state === 'missing' || state === 'unsupported') && canMake && (
          <button type="button" className={buttonClass()} disabled={busy} onClick={() => void make()}>
            Make the subtitled copy
          </button>
        )}
      </div>
      {state === 'ready' && reachable && (
        <div className="watch-card-actions">
          <label>
            Share link for{' '}
            <select value={shareDays} onChange={(e) => setShareDays(Number(e.target.value) as ShareDays)}>
              {SHARE_CHOICES.map((c) => (
                <option key={c.days} value={c.days}>{c.label}</option>
              ))}
            </select>
          </label>
          <button type="button" className={buttonClass()} disabled={busy} onClick={() => void share()}>
            Make a share link
          </button>
          <button type="button" className={buttonClass('danger')} onClick={() => void endShared()}>End shared links</button>
          {shared && (
            <>
              <input readOnly aria-label="Share link" value={shared.url} onFocus={(e) => e.currentTarget.select()} />
              <button type="button" className={buttonClass()} onClick={() => void copyShared()}>Copy</button>
              <p>{shareNote(shared.days)}</p>
            </>
          )}
        </div>
      )}
      <video ref={holder} hidden muted preload="none" playsInline />
    </section>
  )
}
