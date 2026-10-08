/*
 * StreamEmbed: the Live page's optional picture of the stream, in an iframe
 * built from a validated id (embedLogic.ts), played a few seconds behind the
 * live edge so it lines up with the translation. Kept in one component so a
 * server relay can replace it. Everything that keeps playing (the iframe, the
 * message listener, the timers) goes away when this unmounts, and the parent
 * mounts it only while the session runs.
 */
import { useEffect, useRef, useState } from 'react'

import {
  AUTOPLAY_WAIT_S, DVR_WAIT_S, MUTED_NOTE, NO_DELAY_NOTE, WAITING_NOTE, YT_ORIGIN, canDelay, delayNote, delayReached, embedSrc,
  UNREACHABLE_NOTE, notStarted, parseYouTubeInfo, planDelay, probeOffset, ytCommand, ytListenMessage, ytSeekMessage, type PlayerInfo, type StreamRef,
} from './embedLogic'

/** Seconds to wait for the player to confirm a seek before sending it again. */
const SEEK_CONFIRM_S = 4
const SEEK_TRIES = 3
/** Seconds a probe seek to the live edge gets before the player's clock is read. */
const PROBE_SETTLE_S = 4
/** Seconds the probe first steps back, so a playhead already at the edge still shows that seeks are obeyed. */
const PROBE_BACK_S = 30

export function StreamEmbed({ stream, delay }: { stream: StreamRef; delay: number }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const delayRef = useRef(delay)
  const [muted, setMuted] = useState(false)
  const unmuteRef = useRef<() => void>(() => {})
  const [note, setNote] = useState(canDelay(stream) ? WAITING_NOTE : NO_DELAY_NOTE)
  // Set by the effect below; re-applies the delay (seek) from the last report.
  const applyRef = useRef<() => void>(() => {})
  const key = stream.kind === 'twitch-channel' ? stream.name : stream.id

  useEffect(() => {
    if (stream.kind !== 'youtube') return
    let info: PlayerInfo | null = null
    let heard = false
    let waited = 0
    let appliedFor: number | null = null
    // A seek is "moving" until the player's own report shows the delay reached.
    let moving = false
    let sinceSeek = 0
    let tries = 0
    let unsupported = false
    // Set when the seeks never brought the player to the wanted delay, so no figure is shown for it.
    let unreachable = false
    // How far `duration` sits past the playhead's clock; found once by a probe seek to the live edge.
    let offset = 0
    let probed = false
    // 0: not probing, 1: stepping back, 2: seeking to the live edge.
    let probing = 0
    let probeFrom: number | undefined
    let sinceProbe = 0
    let playAsked = false
    let unstartedS = 0
    let alive = true
    setNote(WAITING_NOTE)
    setMuted(false)
    const send = (msg: string) => frame.current?.contentWindow?.postMessage(msg, YT_ORIGIN)
    const show = () => {
      if (unreachable && !moving && !delayReached(info, delayRef.current, offset)) setNote(UNREACHABLE_NOTE)
      else setNote(delayNote(info, delayRef.current, { unsupported, moving, offset }))
    }
    const apply = () => {
      if (!alive) return
      // An unstarted player drops seeks and reports no window, so wait for it to play.
      if (notStarted(info)) {
        show()
        return
      }
      if (probing) {
        show()
        return
      }
      const plan = planDelay(info, delayRef.current, waited, offset)
      unsupported = plan.kind === 'unsupported'
      if (plan.kind === 'seek') {
        if (appliedFor !== delayRef.current) {
          appliedFor = delayRef.current
          unreachable = false
          moving = true
          sinceSeek = 0
          send(ytSeekMessage(plan.to))
        }
      }
      show()
    }
    applyRef.current = () => {
      // Not reset to null: the mount-time call must not repeat a seek already sent for this delay.
      if (appliedFor !== delayRef.current) tries = 1
      apply()
    }
    unmuteRef.current = () => {
      send(ytCommand('unMute'))
      setMuted(false)
    }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== YT_ORIGIN || e.source !== frame.current?.contentWindow) return
      const got = parseYouTubeInfo(e.data)
      if (!got) return
      heard = true
      info = { ...info, ...got }
      if (moving && !probing && delayReached(info, delayRef.current, offset)) moving = false
      apply()
    }
    window.addEventListener('message', onMessage)
    // The player only reports once it has been told someone is listening.
    const timer = setInterval(() => {
      if (!heard) send(ytListenMessage())
      else if (notStarted(info)) {
        // The autoplay parameter may be ignored: ask once with sound, then fall back to muted.
        unstartedS += 1
        if (!playAsked) {
          playAsked = true
          send(ytCommand('playVideo'))
        } else if (unstartedS === AUTOPLAY_WAIT_S) {
          send(ytCommand('mute'))
          send(ytCommand('playVideo'))
          setMuted(true)
        }
      }
      // The wait for a seekable window only runs while the player is playing.
      if (!notStarted(info)) waited += 1
      if (moving && !probing && ++sinceSeek >= SEEK_CONFIRM_S) {
        // A seek sent before the player was ready is silently dropped, so ask again a few times.
        if (tries < SEEK_TRIES) {
          tries += 1
          appliedFor = null
        } else if (!probed && info?.duration !== undefined) {
          // The seeks were heard but the playhead is not where duration says: ask for the live edge itself and read the player's own clock there.
          probed = true
          probing = 1
          sinceProbe = 0
          probeFrom = info.currentTime
          send(ytSeekMessage(Math.max(0, (info.currentTime ?? 0) - PROBE_BACK_S)))
        } else {
          moving = false
          unreachable = !delayReached(info, delayRef.current, offset)
        }
      }
      if (probing === 1 && ++sinceProbe >= PROBE_SETTLE_S) {
        // Only an obeyed step back proves the later jump to the edge is a real measurement.
        const t = info?.currentTime
        if (probeFrom !== undefined && t !== undefined && probeFrom - t >= PROBE_BACK_S / 2 && info?.duration !== undefined) {
          probing = 2
          sinceProbe = 0
          probeFrom = t
          send(ytSeekMessage(info.duration))
        } else {
          probing = 0
          moving = false
          unreachable = true
        }
      } else if (probing === 2 && ++sinceProbe >= PROBE_SETTLE_S) {
        probing = 0
        const found = probeOffset(probeFrom, info)
        if (found === null) {
          moving = false
          unreachable = true
        } else {
          offset = found
          appliedFor = null
          tries = 1
        }
      }
      if (waited <= DVR_WAIT_S + 1 || heard) apply()
    }, 1000)
    return () => {
      alive = false
      window.removeEventListener('message', onMessage)
      clearInterval(timer)
    }
  }, [stream.kind, key])

  useEffect(() => {
    delayRef.current = delay
    const t = setTimeout(() => applyRef.current(), 300)
    return () => clearTimeout(t)
  }, [delay])

  return (
    <>
      <div className="live-video-frame">
        <iframe
          ref={frame}
          src={embedSrc(stream, window.location.hostname)}
          title="Stream video"
          sandbox="allow-scripts allow-same-origin allow-presentation"
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
          loading="eager"
          onLoad={() => stream.kind === 'youtube' && frame.current?.contentWindow?.postMessage(ytListenMessage(), YT_ORIGIN)}
        />
      </div>
      <p className="muted live-video-note" data-testid="live-video-note" aria-live="polite">{note}</p>
      {muted && (
        <p className="muted live-video-note" data-testid="live-video-muted">
          {MUTED_NOTE} <button type="button" onClick={() => unmuteRef.current()}>Unmute</button>
        </p>
      )}
    </>
  )
}
