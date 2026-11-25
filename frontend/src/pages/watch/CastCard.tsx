// The Watch page's "Cast to TV": sends the title's English-subtitled copy to a Chromecast or smart TV
// through the browser's own cast picker, by a short-lived link the TV can fetch without signing in.
import { useCallback, useEffect, useRef, useState } from 'react'

import { getCastStatus, makeCastLink } from '../../api/cast'
import { startBurnedVideo } from '../../api/export'
import { buttonClass } from '../../components/uiClasses'
import type { CastStatus } from '../../types/cast'
import { castLabel, castUrl, reachableByTv } from './castLogic'

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
  const holder = useRef<HTMLVideoElement | null>(null)
  const show = supported && canCast && reachableByTv(window.location.hostname)

  const look = useCallback(async () => {
    try {
      setState((await getCastStatus(id)).state)
    } catch {
      setState(null)
    }
  }, [id])

  useEffect(() => {
    if (show) void look()
  }, [id, show, look])

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

  return (
    <section className="card watch-cast" aria-label="Cast to TV">
      <p><strong>Cast to TV</strong></p>
      <p role="status">{note || castLabel(state)}</p>
      <div className="watch-card-actions">
        {state === 'ready' && (
          <button type="button" className={buttonClass('primary')} disabled={busy} onClick={() => void cast()}>
            Cast to TV
          </button>
        )}
        {(state === 'missing' || state === 'unsupported') && canMake && (
          <button type="button" className={buttonClass()} disabled={busy} onClick={() => void make()}>
            Make the subtitled copy
          </button>
        )}
      </div>
      <video ref={holder} hidden muted preload="none" playsInline />
    </section>
  )
}
