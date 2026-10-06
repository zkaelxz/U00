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
  DVR_WAIT_S, NO_DELAY_NOTE, WAITING_NOTE, YT_ORIGIN, canDelay, delayNote, delayReached, embedSrc, parseYouTubeInfo, planDelay,
  ytListenMessage, ytSeekMessage, type PlayerInfo, type StreamRef,
} from './embedLogic'

/** Seconds to wait for the player to confirm a seek before sending it again. */
const SEEK_CONFIRM_S = 4
const SEEK_TRIES = 3

export function StreamEmbed({ stream, delay }: { stream: StreamRef; delay: number }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const delayRef = useRef(delay)
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
    let alive = true
    setNote(WAITING_NOTE)
    const send = (msg: string) => frame.current?.contentWindow?.postMessage(msg, YT_ORIGIN)
    const show = () => setNote(delayNote(info, delayRef.current, { unsupported, moving }))
    const apply = () => {
      if (!alive) return
      const plan = planDelay(info, delayRef.current, waited)
      unsupported = plan.kind === 'unsupported'
      if (plan.kind === 'seek') {
        if (appliedFor !== delayRef.current) {
          appliedFor = delayRef.current
          moving = true
          sinceSeek = 0
          send(ytSeekMessage(plan.to))
        }
      }
      show()
    }
    applyRef.current = () => {
      appliedFor = null
      tries = 1
      apply()
    }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== YT_ORIGIN || e.source !== frame.current?.contentWindow) return
      const got = parseYouTubeInfo(e.data)
      if (!got) return
      heard = true
      info = { ...info, ...got }
      if (moving && delayReached(info, delayRef.current)) moving = false
      apply()
    }
    window.addEventListener('message', onMessage)
    // The player only reports once it has been told someone is listening.
    const timer = setInterval(() => {
      waited += 1
      if (!heard) send(ytListenMessage())
      if (moving && ++sinceSeek >= SEEK_CONFIRM_S) {
        // A seek sent before the player was ready is silently dropped, so ask again a few times.
        if (tries < SEEK_TRIES) {
          tries += 1
          appliedFor = null
        } else moving = false
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
    </>
  )
}
