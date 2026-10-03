/*
 * StreamEmbed: the Live page's optional picture of the stream, in an iframe
 * built from a validated id (streamEmbed.ts), played a few seconds behind the
 * live edge so it lines up with the translation. Kept in one component so a
 * server relay can replace it. Everything that keeps playing (the iframe, the
 * message listener, the timers) goes away when this unmounts, and the parent
 * mounts it only while the session runs.
 */
import { useEffect, useRef, useState } from 'react'

import {
  DVR_WAIT_S, YT_ORIGIN, canDelay, embedSrc, parseYouTubeInfo, planDelay, ytListenMessage, ytSeekMessage,
  type PlayerInfo, type StreamRef,
} from './streamEmbed'

type DelayState = 'waiting' | 'delayed' | 'none'

export const NO_DELAY_NOTE = "This stream can't be delayed, so the picture runs ahead of the lines."

export function StreamEmbed({ stream, delay }: { stream: StreamRef; delay: number }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const delayRef = useRef(delay)
  const [state, setState] = useState<DelayState>(canDelay(stream) ? 'waiting' : 'none')
  // Set by the effect below; re-applies the delay (seek) from the last report.
  const applyRef = useRef<() => void>(() => {})
  const key = stream.kind === 'twitch-channel' ? stream.name : stream.id

  useEffect(() => {
    if (stream.kind !== 'youtube') return
    let info: PlayerInfo | null = null
    let heard = false
    let waited = 0
    let appliedFor: number | null = null
    let alive = true
    const send = (msg: string) => frame.current?.contentWindow?.postMessage(msg, YT_ORIGIN)
    const apply = () => {
      if (!alive) return
      const plan = planDelay(info, delayRef.current, waited)
      if (plan.kind === 'seek') {
        if (appliedFor !== delayRef.current) {
          appliedFor = delayRef.current
          send(ytSeekMessage(plan.to))
        }
        setState('delayed')
      } else {
        setState(plan.kind === 'unsupported' ? 'none' : 'waiting')
      }
    }
    applyRef.current = () => {
      appliedFor = null
      apply()
    }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== YT_ORIGIN || e.source !== frame.current?.contentWindow) return
      const got = parseYouTubeInfo(e.data)
      if (!got) return
      heard = true
      info = { ...info, ...got }
      apply()
    }
    window.addEventListener('message', onMessage)
    // The player only reports once it has been told someone is listening.
    const timer = setInterval(() => {
      waited += 1
      if (!heard) send(ytListenMessage())
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
          allow="autoplay; encrypted-media; picture-in-picture"
          referrerPolicy="strict-origin-when-cross-origin"
          loading="eager"
          onLoad={() => stream.kind === 'youtube' && frame.current?.contentWindow?.postMessage(ytListenMessage(), YT_ORIGIN)}
        />
      </div>
      {state === 'none' && <p className="muted live-video-note" data-testid="live-video-note">{NO_DELAY_NOTE}</p>}
      {state === 'delayed' && <p className="muted live-video-note" data-testid="live-video-note">{`Playing about ${delay} s behind live.`}</p>}
    </>
  )
}
