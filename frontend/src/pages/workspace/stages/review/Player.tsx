import { useCallback, useEffect, useId, useImperativeHandle, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode, type Ref, type SyntheticEvent } from 'react'

import { createPortal } from 'react-dom'

import { mediaStreamUrl, type MediaKind } from '../../../../api/media'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { usePopOut } from '../../../../hooks/usePopOut'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import type { ReviewLine } from '../../../../types/review'
import { formatDuration, formatTime } from './reviewLogic'
import { clampTime, JUMP_ERROR, lineAt, parseJumpTime, SUBTITLE_OPTIONS, subtitleSrc, type SubtitleChoice } from './playerLogic'
import { lineNumber } from '../../../../lineNumber'

export interface PlayerHandle {
  // Seek to the line's start and stop at its end (or repeat it with Loop line).
  playLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  // Play or stop the given line: stops when that line is already playing.
  toggleLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  togglePlay: () => void
  toggleLoop: () => void
}

type Segment = Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>
type Timed = Pick<ReviewLine, 'idx' | 'start' | 'end'>

interface Props {
  dramaId: number
  kind: MediaKind
  ref?: Ref<PlayerHandle>
  // The lines on screen (for "Line #N" under the playhead) and the active one.
  lines?: Timed[]
  selected?: Timed | null
  // Bumped after every write, so the subtitles are re-read from the current lines.
  captionVersion?: number
  // Extra controls at the end of the strip (the pager, on phones).
  trailing?: ReactNode
  // Phones: where the video, seek bar and tools go instead (outside the sticky
  // toolbar, so they scroll away); null while that spot isn't mounted yet.
  panelHost?: HTMLElement | null
}

const PLAY_ERROR = 'Couldn’t play the audio. Check the file on Source.'
const NO_SUBS: Record<Exclude<SubtitleChoice, 'off'>, string> = {
  English: 'No English subtitles yet: nothing is translated.',
  Source: 'No original subtitles yet: nothing is transcribed.',
  Bilingual: 'Both-language subtitles need an original and a translation.',
}

// A player over the Range endpoint, with a seek bar, jump to time and
// subtitles from the current lines (the Reader's caption route). Only rendered
// when the drama has audio (or a source video); the element fetches the stream itself.
export function Player({ dramaId, kind, ref, lines = [], selected = null, captionVersion = 0, trailing, panelHost }: Props) {
  const media = useRef<HTMLMediaElement | null>(null)
  const trackEl = useRef<HTMLTrackElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(NaN)
  const [segment, setSegment] = useState<Segment | null>(null)
  const [failed, setFailed] = useState(false)
  const [loop, setLoop] = usePersistedState('review.loop', false)
  const [open, setOpen] = usePersistedState('review.playerOpen', true)
  const [subsPref, setSubs] = usePersistedState<string>('review.subs', 'English')
  const subs: SubtitleChoice = SUBTITLE_OPTIONS.some((o) => o.value === subsPref) ? (subsPref as SubtitleChoice) : 'English'
  // Keyed on the track URL, so a new track starts blank without an effect reset.
  const [cueFor, setCueFor] = useState<{ src: string; text: string } | null>(null)
  const [missingSrc, setMissingSrc] = useState<string | null>(null)
  const [jump, setJump] = useState('')
  const [jumpError, setJumpError] = useState<string | null>(null)
  const loopId = useId()
  const loopRef = useRef(loop)
  const segRef = useRef<Segment | null>(null)
  // The panel (with the <video>) is portalled into one element for the
  // player's whole life, and that element is moved between the inline slot and
  // the phone dock. Rendering the panel under a different parent instead would
  // remount the <video> when a phone turns or a window crosses 640px, which
  // stops playback and starts it over at 0:00.
  const [panelBox] = useState(() => {
    const box = document.createElement('div')
    box.className = 'review-player-host'
    return box
  })
  const slotRef = useRef<HTMLDivElement | null>(null)
  const src = subtitleSrc(dramaId, subs, captionVersion)
  const cue = cueFor && cueFor.src === src ? cueFor.text : ''
  const subsMissing = src !== null && missingSrc === src

  useEffect(() => {
    loopRef.current = loop
  }, [loop])

  const place = useCallback(() => {
    const target = panelHost === undefined ? slotRef.current : panelHost
    if (target && panelBox.parentNode !== target) target.appendChild(panelBox)
  }, [panelHost, panelBox])
  const pop = usePopOut(panelBox, place)
  const floating = pop.active

  // Move (never detach) the panel: while the dock isn't mounted yet (null) it
  // stays where it is, so the video never leaves the page and keeps playing.
  // While it is in the floating window it stays there.
  useLayoutEffect(() => {
    if (!floating) place()
  }, [place, floating])
  useLayoutEffect(() => () => panelBox.remove(), [panelBox])

  const setSeg = (s: Segment | null) => {
    segRef.current = s
    setSegment(s)
  }

  // While a line plays, stop (or loop) exactly at its end; timeupdate alone
  // fires only about four times a second.
  useEffect(() => {
    if (!playing || !segment) return
    let raf = 0
    const check = () => {
      const el = media.current
      const s = segRef.current
      if (el && s && el.currentTime >= s.end) {
        if (loopRef.current) el.currentTime = s.start
        else {
          el.pause()
          setSeg(null)
          return
        }
      }
      raf = requestAnimationFrame(check)
    }
    raf = requestAnimationFrame(check)
    return () => cancelAnimationFrame(raf)
  }, [playing, segment])

  // The subtitle track: drawn on the video; under an audio player the current
  // cue is shown as text. A fresh <track> (keyed on its URL) re-reads the lines.
  useEffect(() => {
    const t = trackEl.current?.track
    if (!t || !src) return
    t.mode = kind === 'video' ? 'showing' : 'hidden'
    const onCue = () => {
      const active = t.activeCues ? Array.from(t.activeCues) : []
      setCueFor({ src, text: active.map((c) => (c as VTTCue).getCueAsHTML?.().textContent ?? (c as VTTCue).text).join('\n') })
    }
    t.addEventListener('cuechange', onCue)
    return () => t.removeEventListener('cuechange', onCue)
  }, [src, kind])

  const play = useCallback(() => {
    const el = media.current
    if (!el) return
    el.play().catch((e: unknown) => {
      // A pause or a new seek interrupts play(); that is not a failure.
      if ((e as { name?: string } | null)?.name !== 'AbortError') setFailed(true)
    })
  }, [])

  const playLine = useCallback(
    (line: Segment) => {
      const el = media.current
      if (!el) return
      setSeg({ id: line.id, idx: line.idx, start: line.start, end: line.end })
      el.currentTime = line.start
      play()
    },
    [play],
  )

  const stop = useCallback(() => {
    media.current?.pause()
    setSeg(null)
  }, [])

  const togglePlay = useCallback(() => {
    const el = media.current
    if (!el) return
    if (el.paused) play()
    else el.pause()
  }, [play])

  // A manual seek leaves line playback: playing carries on from the new spot.
  const seekTo = (seconds: number) => {
    const el = media.current
    if (!el) return
    const t = clampTime(seconds, duration)
    setSeg(null)
    el.currentTime = t
    setTime(t)
  }

  const submitJump = (e: FormEvent) => {
    e.preventDefault()
    const t = parseJumpTime(jump)
    if (t === null) {
      setJumpError(JUMP_ERROR)
      return
    }
    setJumpError(null)
    seekTo(t)
  }

  useImperativeHandle(
    ref,
    () => ({
      playLine,
      toggleLine: (line) => {
        const el = media.current
        if (el && !el.paused && segRef.current?.id === line.id) stop()
        else playLine(line)
      },
      togglePlay,
      toggleLoop: () => setLoop(!loopRef.current),
    }),
    [playLine, stop, togglePlay, setLoop],
  )

  const track = src ? (
    <track
      key={src}
      ref={trackEl}
      kind="subtitles"
      label={SUBTITLE_OPTIONS.find((o) => o.value === subs)?.label}
      srcLang={subs === 'English' ? 'en' : 'und'}
      src={src}
      default
      onError={() => setMissingSrc(src)}
    />
  ) : null

  const common = {
    src: mediaStreamUrl(dramaId, kind),
    preload: 'metadata' as const,
    onPlay: () => {
      setPlaying(true)
      setFailed(false)
    },
    onPause: () => setPlaying(false),
    onEnded: () => {
      setPlaying(false)
      setSeg(null)
    },
    onTimeUpdate: (e: SyntheticEvent<HTMLMediaElement>) => setTime(e.currentTarget.currentTime),
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => setDuration(e.currentTarget.duration),
    onError: () => setFailed(true),
  }
  const here = segment ?? lineAt(lines, time)
  const known = Number.isFinite(duration) && duration > 0

  // Kept mounted while folded away, so the sound and the strip above still work.
  const panel = (
    <div className="review-player-panel" hidden={!open && !floating}>
      {kind === 'video' ? (
        <video
          ref={(el) => {
            media.current = el
          }}
          className="review-video"
          playsInline
          // A click on the picture plays or pauses; the Play button above stays the keyboard control.
          onClick={failed ? undefined : togglePlay}
          {...common}
        >
          {track}
        </video>
      ) : (
        subs !== 'off' && (
          <p className="review-caption" data-testid="player-caption">
            {cue || '\u00a0'}
          </p>
        )
      )}
      <div className="review-player-controls">
        <input
          type="range"
          className="review-seek"
          aria-label="Seek"
          aria-valuetext={`${formatDuration(time)} of ${formatDuration(duration)}`}
          min={0}
          max={known ? duration : 0}
          step={0.1}
          value={known ? Math.min(time, duration) : 0}
          disabled={!known || failed}
          onChange={(e) => seekTo(Number(e.target.value))}
        />
        <div className="review-player-tools">
          <form className="review-jump" onSubmit={submitJump}>
            <input
              type="text"
              inputMode="decimal"
              enterKeyHint="go"
              autoComplete="off"
              aria-label="Jump to time"
              placeholder="mm:ss"
              title="e.g. 1:23, 1:02:03 or 83.5"
              value={jump}
              onChange={(e) => setJump(e.target.value)}
            />
            <button type="submit" className={buttonClass('secondary', 'sm')} disabled={!jump.trim() || failed}>
              Jump
            </button>
          </form>
          {selected && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => seekTo(selected.start)} disabled={failed}>
              Go to line #{lineNumber(selected.idx)}
            </button>
          )}
          <label className="review-subs">
            Subtitles
            <select value={subs} onChange={(e) => setSubs(e.target.value)}>
              {SUBTITLE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {jumpError && (
        <p className="error" role="alert">
          {jumpError}
        </p>
      )}
      {subsMissing && subs !== 'off' && <p className="muted">{NO_SUBS[subs]}</p>}
    </div>
  )

  return (
    <div className="review-player" role="group" aria-label="Player">
      <div className="review-player-row">
        <button
          type="button"
          className={buttonClass('secondary', 'md', 'review-play')}
          aria-label={playing ? 'Pause' : 'Play'}
          title={playing ? 'Pause (Alt+Space)' : 'Play (Alt+Space)'}
          onClick={togglePlay}
          disabled={failed}
        >
          {playing ? '❚❚' : '▶'}
        </button>
        <span className="review-time" data-testid="player-time">
          {formatTime(time)}
          <span className="review-duration"> / {formatDuration(duration)}</span>
        </span>
        {here && <span className="muted" data-testid="player-line">Line #{lineNumber(here.idx)}</span>}
        <span className="review-loop">
          <Toggle id={loopId} checked={loop} onChange={setLoop} aria-label="Loop line" title="Repeat the line being played (L)" />
          <label htmlFor={loopId} aria-hidden="true">
            Loop<span className="review-loop-word"> line</span>
          </label>
        </span>
        <button type="button" className={buttonClass('ghost', 'sm', 'review-player-toggle')} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide player' : 'Show player'}
        </button>
        {pop.supported && (
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={floating ? pop.close : () => void pop.open()} title="Keep the player in a small window that stays on top">
            {floating ? 'Return player' : 'Pop out'}
          </button>
        )}
        {trailing}
      </div>
      {failed && (
        <p className="error" role="alert">
          {PLAY_ERROR}
        </p>
      )}
      <div className="review-player-slot" ref={slotRef} />
      {createPortal(panel, panelBox)}
      {kind === 'audio' && (
        <audio
          ref={(el) => {
            media.current = el
          }}
          {...common}
        >
          {track}
        </audio>
      )}
    </div>
  )
}
