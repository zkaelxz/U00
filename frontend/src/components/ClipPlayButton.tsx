/*
 * ClipPlayButton: a small Play/Stop toggle for one short audio clip (a
 * voice-bank entry, a speaker's sample), streamed from `src`, which needs
 * media access. Only one clip plays at a time on the page; the clip loads
 * on the first press. Key it by the clip where `src` can change under the
 * same slot.
 */
import { useEffect, useRef, useState } from 'react'

import { buttonClass } from './uiClasses'

let playing: { audio: HTMLAudioElement; stop: () => void } | null = null

const VOICE_PLAY_FAILED = "Couldn't play this clip (it may be missing, or media access is off)."

export function ClipPlayButton({ src, name, text = 'Play', title }: {
  src: string
  // Read out as "Play <name>" / "Stop <name>".
  name: string
  text?: string
  title?: string
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [on, setOn] = useState(false)
  const [failed, setFailed] = useState(false)

  const stop = () => {
    const a = audioRef.current
    if (a) {
      a.pause()
      a.currentTime = 0
    }
    if (playing?.audio === a) playing = null
    setOn(false)
  }
  useEffect(() => () => {
    const a = audioRef.current
    if (a) a.pause()
    if (playing?.audio === a) playing = null
  }, [])
  const toggle = () => {
    if (on) {
      stop()
      return
    }
    let a = audioRef.current
    if (!a) {
      a = new Audio(src)
      a.preload = 'none'
      a.addEventListener('ended', () => setOn(false))
      a.addEventListener('error', () => {
        setOn(false)
        setFailed(true)
      })
      audioRef.current = a
    }
    if (playing && playing.audio !== a) playing.stop()
    playing = { audio: a, stop }
    setFailed(false)
    setOn(true)
    a.play().catch((e: unknown) => {
      // Another clip's Play (or Stop) paused this one before it started;
      // that is not a failure to report.
      if (e instanceof DOMException && e.name === 'AbortError') return
      setOn(false)
      setFailed(true)
    })
  }

  return (
    <span className="voice-play">
      <button type="button" className={buttonClass('ghost', 'sm')} aria-pressed={on}
        aria-label={on ? `Stop ${name}` : `Play ${name}`} title={title} onClick={toggle}>
        {on ? '■ Stop' : `▶ ${text}`}
      </button>
      {failed && <span className="muted voice-play-error" role="status">{VOICE_PLAY_FAILED}</span>}
    </span>
  )
}
