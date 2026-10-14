/*
 * DelayedPlayer: the Live page's own picture of the stream, played from the
 * rolling copy the server records (live_player.py), so the picture can be held
 * back for any stream, including ones the YouTube embed cannot rewind. Chosen
 * with the "Delayed player" option; the embed stays the default. hls.js loads
 * only when this mounts. Everything it starts (player, timer) goes away on unmount.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'

import { MUTED_NOTE } from './embedLogic'
import {
  PREPARING_NOTE, RECOVERING_NOTE, RECORDER_LAG_S, SEEK_COOLDOWN_S, UNSUPPORTED_NOTE, errorAction, placement, playerSrc, stallAction,
} from './playerLogic'

export function DelayedPlayer({ sessionId, ready, delay, captions }: {
  sessionId: string
  ready: boolean
  delay: number
  captions?: (effectiveDelay: number) => ReactNode
}) {
  const video = useRef<HTMLVideoElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const delayRef = useRef(delay)
  // Where the picture was last placed (seconds behind the newest recording); null = place it again.
  const goalRef = useRef<number | null>(null)
  const [note, setNote] = useState(PREPARING_NOTE)
  const [muted, setMuted] = useState(false)
  // Seconds the picture plays behind real live (behind the newest recorded second, plus the recorder's lag); the captions wait that long.
  const [pictureDelay, setPictureDelay] = useState(0)
  // Bumped to throw the player away and start it again after an error it could not recover from.
  const [attempt, setAttempt] = useState(0)
  const captionNode = captions?.(pictureDelay)

  useEffect(() => {
    delayRef.current = delay
    goalRef.current = null
  }, [delay])

  useEffect(() => {
    const v = video.current
    if (!ready || !v) {
      setNote(PREPARING_NOTE)
      return
    }
    let alive = true
    let timer: ReturnType<typeof setInterval> | undefined
    let hls: { destroy: () => void; startLoad: () => void; recoverMediaError: () => void } | null = null
    let lastSeekAt = -Infinity
    let lastTime = -1
    let stalledS = 0
    const rebuild = () => alive && setAttempt((n) => n + 1)

    const tick = () => {
      if (!alive || v.seekable.length === 0) return
      const start = v.seekable.start(0)
      const end = v.seekable.end(v.seekable.length - 1)
      const now = performance.now() / 1000
      const place = placement(start, end, v.currentTime, delayRef.current, goalRef.current)
      goalRef.current = place.goal
      // The held goal, not the measure: that steps by a segment and would make the captions jump.
      setPictureDelay(Math.max(0, Math.round((place.goal ?? end - v.currentTime) + RECORDER_LAG_S)))
      if (place.to !== null && now - lastSeekAt >= SEEK_COOLDOWN_S) {
        lastSeekAt = now
        v.currentTime = place.to
      }
      const moving = Math.abs(v.currentTime - lastTime) > 0.05
      lastTime = v.currentTime
      stalledS = moving || v.paused ? 0 : stalledS + 1
      const action = stallAction(stalledS)
      if (action === 'nudge') {
        setNote(RECOVERING_NOTE)
        hls?.startLoad()
      } else if (action === 'rebuild') {
        rebuild()
      } else if (moving) {
        setNote('')
      }
    }

    void (async () => {
      const { default: Hls } = await import('hls.js')
      if (!alive) return
      const src = playerSrc(sessionId)
      if (Hls.isSupported()) {
        const player = new Hls({ maxBufferLength: 30, backBufferLength: 90 })
        hls = player
        player.on(Hls.Events.ERROR, (_event, data) => {
          const action = errorAction(data.fatal, data.type)
          if (action === 'reload') player.startLoad()
          else if (action === 'recover-media') player.recoverMediaError()
          else if (action === 'rebuild') rebuild()
        })
        player.loadSource(src)
        player.attachMedia(v)
      } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
        v.src = src
      } else {
        setNote(UNSUPPORTED_NOTE)
        return
      }
      // Browsers refuse sound before a click; start muted then and say so.
      v.play().catch(() => {
        v.muted = true
        setMuted(true)
        v.play().catch(() => {})
      })
      timer = setInterval(tick, 1000)
    })()

    return () => {
      alive = false
      if (timer) clearInterval(timer)
      hls?.destroy()
      v.removeAttribute('src')
      v.load()
    }
  }, [sessionId, ready, attempt])

  return (
    <>
      <div className="live-video-frame" ref={box}>
        <video ref={video} controls playsInline data-testid="live-delayed-video" />
        {captionNode}
      </div>
      {captionNode && document.fullscreenEnabled && (
        <p className="muted live-video-note">
          <button type="button" onClick={() => void box.current?.requestFullscreen().catch(() => {})}>Full screen with captions</button>
        </p>
      )}
      <p className="muted live-video-note" data-testid="live-video-note" aria-live="polite">{note}</p>
      {muted && (
        <p className="muted live-video-note" data-testid="live-video-muted">
          {MUTED_NOTE} <button type="button" onClick={() => { if (video.current) video.current.muted = false; setMuted(false) }}>Unmute</button>
        </p>
      )}
    </>
  )
}
