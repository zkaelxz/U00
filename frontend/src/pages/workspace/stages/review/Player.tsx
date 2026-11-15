import { useCallback, useContext, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type Ref, type RefObject, type SyntheticEvent } from 'react'

import { createPortal } from 'react-dom'

import { mediaStreamUrl, type PlayerMediaKind } from '../../../../api/media'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { usePopOut } from '../../../../hooks/usePopOut'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import type { ReviewLine } from '../../../../types/review'
import { formatDuration, formatTime } from './reviewLogic'
import { CAPTION_SIZE_OPTIONS, clampTime, FULLSCREEN_IDLE_MS, JUMP_ERROR, lineAt, overlayText, parseJumpTime, PLAYBACK_RATES, jumpPlaceholder, playerKeyAction, popoutCaptionPx, SUBTITLE_OPTIONS, subtitleSrc, validRate, type CaptionSize, type SubtitleChoice } from './playerLogic'
import { lineNumber } from '../../../../lineNumber'
import { StageContext } from '../../StageContext'
import { stageLabel } from '../../stages'
import { titleKind } from '../../titleKind'
import { FloatingFrame } from './FloatingFrame'
import { PopOutButton } from './PopOutButton'
import { useFloatBox } from './useFloatBox'

export interface PlayerHandle {
  // Seek to the line's start and stop at its end (or repeat it with Loop line).
  playLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  // Play or stop the given line: stops when that line is already playing.
  toggleLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  togglePlay: () => void
  getCurrentTime: () => number
  setRate: (rate: number) => void
  toggleLoop: () => void
  // For the waveform: read the clock without re-rendering, and seek like the seek bar.
  getTime: () => number
  getDuration: () => number
  seek: (seconds: number) => void
  // The picture on screen as a PNG, after seeking to `at` when given; null when
  // there is no decoded picture (audio, not loaded yet, or a browser that blocks reading it).
  captureFrame: (at?: number) => Promise<Blob | null>
  // For a playhead drawn elsewhere: whether the media plays, and a call each time it starts or stops, so a frame loop
  // runs only while it plays. Returns the unsubscribe.
  isPlaying: () => boolean
  onPlayChange: (listener: () => void) => () => void
}

type Segment = Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>
type Timed = Pick<ReviewLine, 'idx' | 'start' | 'end'>

interface Props {
  dramaId: number
  kind: PlayerMediaKind
  ref?: Ref<PlayerHandle>
  // The strip's id, for a button elsewhere that shows or hides it.
  id?: string
  // Told when playing fails, so a player folded out of sight can come out with its error.
  onFailed?: () => void
  // The lines on screen (for "Line #N" under the playhead) and the active one.
  lines?: Timed[]
  selected?: Timed | null
  // Bumped after every write, so the subtitles are re-read from the current lines.
  captionVersion?: number
  // Every line is known to have no translation, so the English track does not exist: it is not requested (the server answers 404 for it).
  englishAbsent?: boolean
  // Extra controls at the end of the strip (the pager, on phones).
  trailing?: ReactNode
  // Phones: where the video, seek bar and tools go instead (outside the sticky
  // toolbar, so they scroll away); null while that spot isn't mounted yet.
  panelHost?: HTMLElement | null
  // Outside a title's workspace (the Watch page) there is no stage context to name the Media step from.
  sourceStage?: string
  // The Watch page is the player, so it is never folded away there.
  alwaysOpen?: boolean
  // The page shows the spoken line itself, larger, so a sound-only title's caption strip (and the choice that only
  // changes it) would repeat it; the pop-out window and the floating box keep both, as the only text there.
  lineShownByPage?: boolean
  // A page that plays by seeking rather than line by line (the Watch page) names the line under the playhead, which
  // Loop line then repeats. Stable, so the player does not redraw for it.
  loopLineAt?: (time: number) => Segment | null
}

// The Fullscreen API is still webkit-prefixed in older Safari.
type FsDoc = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void; webkitFullscreenEnabled?: boolean }
type FsEl = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void }
type FsVideo = HTMLVideoElement & { webkitEnterFullscreen?: () => void }

// iPhone Safari has no element fullscreen but can take the <video> itself.
const fullscreenAvailable = () => document.fullscreenEnabled || !!(document as FsDoc).webkitFullscreenEnabled || 'webkitEnterFullscreen' in HTMLVideoElement.prototype

const fullscreenElementOf = (doc: Document) => doc.fullscreenElement ?? (doc as FsDoc).webkitFullscreenElement ?? null

const playError = (sourceStage: string) => `Couldn’t play the audio. Check the file on ${sourceStage}.`
const NO_SUBS: Record<Exclude<SubtitleChoice, 'off'>, string> = {
  English: 'No English subtitles yet: nothing is translated.',
  Source: 'No original subtitles yet: nothing is transcribed.',
  Bilingual: 'Both-language subtitles need an original and a translation.',
}

// Whether the media plays or has failed, and what the page around the player hears of it: each start and stop, and
// every failed attempt, so a player folded out of sight comes out with its error. Every attempt, not only the first: a
// player folded again after failing stays failed, and the next Play from outside it must bring it back.
function usePlayState(onFailed?: () => void) {
  const [playing, setPlaying] = useState(false)
  const [failed, setFailed] = useState(false)
  const playingNow = useRef(false)
  const listeners = useRef(new Set<() => void>())
  useEffect(() => {
    playingNow.current = playing
    listeners.current.forEach((listener) => listener())
  }, [playing])
  const fail = useCallback(() => {
    setFailed(true)
    onFailed?.()
  }, [onFailed])
  const outside = useMemo<Pick<PlayerHandle, 'isPlaying' | 'onPlayChange'>>(() => ({
    isPlaying: () => playingNow.current,
    onPlayChange: (listener) => {
      listeners.current.add(listener)
      return () => {
        listeners.current.delete(listener)
      }
    },
  }), [])
  return { playing, setPlaying, failed, setFailed, fail, outside }
}

// A player over the Range endpoint, with a seek bar, jump to time and
// subtitles from the current lines (the Reader's caption route). Only rendered
// when the drama has audio (or a source video); the element fetches the stream itself.
export function Player({ dramaId, kind, ref, id, onFailed, lines = [], selected = null, captionVersion = 0, englishAbsent = false, trailing, panelHost, sourceStage, alwaysOpen = false, lineShownByPage = false, loopLineAt }: Props) {
  const stage = useContext(StageContext)
  const media = useRef<HTMLMediaElement | null>(null)
  const trackEl = useRef<HTMLTrackElement | null>(null)
  const { playing, setPlaying, failed, setFailed, fail, outside } = usePlayState(onFailed)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(NaN)
  const [segment, setSegment] = useState<Segment | null>(null)
  const [loop, setLoop] = useLoopSwitch(loopLineAt !== undefined)
  const [open, setOpen] = usePersistedState('review.playerOpen', !(useMediaQuery('(max-width: 640px)') && kind === 'video')) // closed on a phone: an open video hides the lines
  const [ratePref, setRatePref] = usePersistedState('review.rate', 1)
  const rate = validRate(ratePref)
  const [subsPref, setSubs] = usePersistedState<string>('review.subs', 'English')
  const subs: SubtitleChoice = SUBTITLE_OPTIONS.some((o) => o.value === subsPref) ? (subsPref as SubtitleChoice) : 'English'
  // Keyed on the track URL, so a new track starts blank without an effect reset.
  const [cueFor, setCueFor] = useState<{ src: string; text: string } | null>(null)
  const [missingSrc, setMissingSrc] = useState<string | null>(null)
  const [captionSizePref, setCaptionSize] = usePersistedState<string>('review.popoutCaptionSize', 'default')
  const captionSize: CaptionSize = CAPTION_SIZE_OPTIONS.some((o) => o.value === captionSizePref) ? (captionSizePref as CaptionSize) : 'default'
  const [jump, setJump] = useState('')
  const [jumpFocused, setJumpFocused] = useState(false)
  const [jumpError, setJumpError] = useState<string | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)
  const controlsRef = useRef<HTMLDivElement | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [idle, setIdle] = useState(false)
  const idleTimer = useRef(0)
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
  const src = englishAbsent && subs === 'English' ? null : subtitleSrc(dramaId, subs, captionVersion)
  const cue = cueFor && cueFor.src === src ? cueFor.text : ''
  const subsMissing = src === null ? subs === 'English' && englishAbsent : missingSrc === src

  // Also on loadedmetadata: a new source can reset the element's speed.
  const applyRate = useCallback((el: HTMLMediaElement | null) => {
    if (!el) return
    el.defaultPlaybackRate = rate
    el.playbackRate = rate
    // Without this a slowed clip sounds low; browsers that lack it keep their default.
    if ('preservesPitch' in el) el.preservesPitch = true
  }, [rate])
  useEffect(() => applyRate(media.current), [applyRate])

  // Browsers without Document Picture-in-Picture get a box inside the page instead.
  const { boxed, setBoxed, boxHost, setBoxHost, boxWidth, setBoxWidth } = useFloatBox()
  const place = useCallback(() => {
    // Until the box has mounted its body the panel stays where it is, so the video keeps playing.
    const target = boxed ? boxHost : panelHost === undefined ? slotRef.current : panelHost
    if (target && panelBox.parentNode !== target) target.appendChild(panelBox)
  }, [panelHost, panelBox, boxed, boxHost])
  const pop = usePopOut(panelBox, place)
  const floating = pop.active

  // Move (never detach) the panel: while the dock isn't mounted yet (null) it
  // stays where it is, so the video never leaves the page and keeps playing.
  // While it is in the floating window it stays there.
  useLayoutEffect(() => {
    if (!floating) place()
  }, [place, floating])
  useLayoutEffect(() => () => panelBox.remove(), [panelBox])

  // The pop-out window is resized by the user, so the caption follows its width.
  // Listening on that window's own resize event (not a ResizeObserver from this
  // page) keeps this independent of cross-document observer support.
  useEffect(() => {
    const win = panelBox.ownerDocument.defaultView
    if (!floating || !win) return
    const fit = () => panelBox.style.setProperty('--popout-caption-size', `${popoutCaptionPx(win.innerWidth, captionSize)}px`)
    fit()
    win.addEventListener('resize', fit)
    return () => {
      win.removeEventListener('resize', fit)
      panelBox.style.removeProperty('--popout-caption-size')
    }
  }, [floating, panelBox, captionSize])

  const setSeg = (s: Segment | null) => {
    segRef.current = s
    setSegment(s)
  }
  useLoop({ loop, loopRef, playing, loopLineAt, media, segRef, setSeg })

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

  // The subtitle track: its current cue is shown over the picture or as text. A fresh <track> (keyed on its URL) re-reads the lines.
  useEffect(() => {
    const t = trackEl.current?.track
    if (!t || !src) return
    // Hidden, so the browser doesn't draw a second copy: the overlay (or, floating or
    // audio, the caption text) shows the cue.
    t.mode = 'hidden'
    const onCue = () => {
      const active = t.activeCues ? Array.from(t.activeCues) : []
      setCueFor({ src, text: active.map((c) => (c as VTTCue).getCueAsHTML?.().textContent ?? (c as VTTCue).text).join('\n') })
    }
    t.addEventListener('cuechange', onCue)
    return () => t.removeEventListener('cuechange', onCue)
  }, [src])

  const play = useCallback(() => {
    const el = media.current
    if (!el) return
    el.play().catch((e: unknown) => {
      // A pause or a new seek interrupts play(); that is not a failure.
      if ((e as { name?: string } | null)?.name !== 'AbortError') fail()
    })
  }, [fail])

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

  // The frame (picture, subtitle overlay and controls) goes full screen, not the
  // bare <video>, so the subtitles stay visible. iPhone Safari only allows the
  // video element, which draws no overlay.
  const toggleFullscreen = useCallback(() => {
    const frame = frameRef.current
    const doc = frame?.ownerDocument as FsDoc | undefined
    if (!frame || !doc) return
    if (fullscreenElementOf(doc)) {
      void (doc.exitFullscreen?.() ?? doc.webkitExitFullscreen?.())
      return
    }
    const nativeVideo = () => (media.current as FsVideo | null)?.webkitEnterFullscreen?.()
    const request = (frame.requestFullscreen ?? (frame as FsEl).webkitRequestFullscreen)?.bind(frame)
    if (!request) {
      nativeVideo()
      return
    }
    try {
      Promise.resolve(request()).catch(nativeVideo)
    } catch {
      nativeVideo()
    }
  }, [])

  useEffect(() => {
    const frame = frameRef.current
    const doc = frame?.ownerDocument
    if (!frame || !doc) return
    const sync = () => setFullscreen(fullscreenElementOf(doc) === frame)
    doc.addEventListener('fullscreenchange', sync)
    doc.addEventListener('webkitfullscreenchange', sync)
    return () => {
      doc.removeEventListener('fullscreenchange', sync)
      doc.removeEventListener('webkitfullscreenchange', sync)
    }
  }, [kind, floating])

  // Full screen hides the controls (and the pointer) after a quiet spell; any
  // movement or key brings them back. They stay while focus is inside them.
  const wake = useCallback(() => {
    setIdle(false)
    const win = frameRef.current?.ownerDocument.defaultView ?? window
    win.clearTimeout(idleTimer.current)
    idleTimer.current = win.setTimeout(function hide() {
      if (controlsRef.current?.contains(controlsRef.current.ownerDocument.activeElement)) {
        idleTimer.current = win.setTimeout(hide, FULLSCREEN_IDLE_MS)
        return
      }
      setIdle(true)
    }, FULLSCREEN_IDLE_MS)
  }, [])
  useEffect(() => {
    if (!fullscreen) return
    wake()
    const win = frameRef.current?.ownerDocument.defaultView ?? window
    return () => {
      win.clearTimeout(idleTimer.current)
      setIdle(false)
    }
  }, [fullscreen, wake])

  const onFrameKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (fullscreen) wake()
    const el = media.current
    if (!el || failed) return
    const action = playerKeyAction({ key: e.key, shiftKey: e.shiftKey, altKey: e.altKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, target: e.target as Element }, el.currentTime, duration)
    if (!action) return
    e.preventDefault()
    if (action.kind === 'seek') seekTo(action.to)
    else if (action.kind === 'playpause') togglePlay()
    else toggleFullscreen()
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
      ...outside,
      playLine,
      toggleLine: (line) => {
        const el = media.current
        if (el && !el.paused && segRef.current?.id === line.id) stop()
        else playLine(line)
      },
      togglePlay,
      getCurrentTime: () => media.current?.currentTime ?? 0,
      setRate: (r) => setRatePref(validRate(r)),
      toggleLoop: () => setLoop(!loopRef.current),
      getTime: () => media.current?.currentTime ?? 0,
      getDuration: () => media.current?.duration ?? NaN,
      seek: seekTo,
      captureFrame: async (at) => {
        const el = media.current
        if (!(el instanceof HTMLVideoElement)) return null
        if (at !== undefined && Math.abs(el.currentTime - at) > 0.02) {
          // A seek to where it already is fires no event, so the wait is bounded.
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 3000)
            el.addEventListener('seeked', () => { clearTimeout(timer); resolve() }, { once: true })
            seekTo(at)
          })
        }
        if (el.readyState < 2 || !el.videoWidth) return null
        const canvas = document.createElement('canvas')
        canvas.width = el.videoWidth
        canvas.height = el.videoHeight
        try {
          canvas.getContext('2d')?.drawImage(el, 0, 0)
          return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
        } catch {
          return null
        }
      },
    }),
    // seekTo reads duration, which only changes with the loaded media.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [playLine, stop, togglePlay, setLoop, setRatePref, duration, outside],
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
    onLoadedMetadata: (e: SyntheticEvent<HTMLMediaElement>) => {
      setDuration(e.currentTarget.duration)
      applyRate(e.currentTarget)
    },
    onError: fail,
  }
  // The played line only names the position while the playhead is still inside it: a seek made
  // outside the app's own controls (keys on the video, a scrub) must not leave its number on screen.
  const here = (segment && time >= segment.start && time <= segment.end ? segment : null) ?? lineAt(lines, time)
  const known = Number.isFinite(duration) && duration > 0

  const overlay = kind === 'video' && !floating && subs !== 'off' ? overlayText(cue) : ''
  const canFullscreen = kind === 'video' && !floating && fullscreenAvailable()
  const textSubs = !(lineShownByPage && kind !== 'video' && !floating && !boxed)

  // Kept mounted while folded away, so the sound and the strip above still work.
  const panel = (
    <div className="review-player-panel" hidden={!open && !alwaysOpen && !floating && !boxed}>
      <div
        ref={frameRef}
        data-testid="player-frame"
        className={['review-player-frame', fullscreen && 'is-fullscreen', fullscreen && idle && 'is-idle'].filter(Boolean).join(' ')}
        {...(kind === 'video'
          ? {
              tabIndex: 0,
              role: 'group',
              'aria-label': 'Video player. Left and right arrows seek, Space plays or pauses, F toggles full screen.',
              'aria-keyshortcuts': 'ArrowLeft ArrowRight Space F',
              onKeyDown: onFrameKey,
              onPointerMove: fullscreen ? wake : undefined,
              onPointerDown: fullscreen ? wake : undefined,
            }
          : {})}
      >
      {kind === 'video' ? (
        <div className="review-stage" onDoubleClick={canFullscreen ? toggleFullscreen : undefined}>
        <video
          ref={(el) => {
            media.current = el
          }}
          className="review-video"
          playsInline
          // The browser's own picture-in-picture is video only; the app's Pop out keeps the subtitles.
          disablePictureInPicture
          // A click on the picture plays or pauses; the Play button above stays the keyboard control.
          onClick={failed ? undefined : togglePlay}
          {...common}
        >
          {track}
        </video>
        {overlay && (
          <div className="review-overlay" data-testid="player-overlay">
            {overlay}
          </div>
        )}
        </div>
      ) : null}
      {(kind !== 'video' || floating) && textSubs && subs !== 'off' && (
        <p className={cue ? 'review-caption' : 'review-caption is-empty'} data-testid="player-caption">
          {cue || '\u00a0'}
        </p>
      )}
      <div className="review-player-controls" ref={controlsRef}>
        {fullscreen && (
          <button type="button" className={buttonClass('secondary', 'md', 'review-fs-play')} aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay} disabled={failed}>
            {playing ? '❚❚' : '▶'}
          </button>
        )}
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
              placeholder={jumpPlaceholder(time, jumpFocused, jump)}
              title="e.g. 1:23, 1:02:03 or 83.5"
              value={jump}
              onFocus={() => setJumpFocused(true)}
              onBlur={() => setJumpFocused(false)}
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
            Speed
            <select value={rate} onChange={(e) => setRatePref(validRate(Number(e.target.value)))}>
              {PLAYBACK_RATES.map((r) => (
                <option key={r} value={r}>
                  {r}×
                </option>
              ))}
            </select>
          </label>
          {textSubs && <label className="review-subs">
            Subtitles
            <select value={subs} onChange={(e) => setSubs(e.target.value)}>
              {SUBTITLE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>}
          {canFullscreen && (
            <button
              type="button"
              className={buttonClass('secondary', 'sm', 'review-fs')}
              aria-pressed={fullscreen}
              aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
              title={fullscreen ? 'Exit full screen (F or Esc)' : 'Full screen (F, or double-click the picture)'}
              onClick={toggleFullscreen}
            >
              <span aria-hidden="true">{fullscreen ? '⤡' : '⤢'}</span>
            </button>
          )}
          {floating && (
            <label className="review-subs">
              Subtitle size
              <select value={captionSize} onChange={(e) => setCaptionSize(e.target.value)}>
                {CAPTION_SIZE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>
      </div>
      {jumpError && (
        <p className="error" role="alert">
          {jumpError}
        </p>
      )}
      {subsMissing && textSubs && subs !== 'off' && <p className="muted">{NO_SUBS[subs]}</p>}
    </div>
  )

  return (
    <div className="review-player" id={id} role="group" aria-label="Player">
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
        {!alwaysOpen && (
          <button type="button" className={buttonClass('ghost', 'sm', 'review-player-toggle')} aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Hide player' : 'Show player'}
          </button>
        )}
        <PopOutButton supported={pop.supported} floating={floating} boxed={boxed} onPop={() => void pop.open()} onReturn={pop.close} onToggleBox={() => setBoxed(!boxed)} />
        {trailing}
      </div>
      {failed && <p className="error" role="alert">{playError(sourceStage ?? stageLabel('source', titleKind(stage?.drama.media_type, stage?.drama.content_mode)))}</p>}
      <div className="review-player-slot" ref={slotRef} />
      {createPortal(panel, panelBox)}
      {boxed && <FloatingFrame width={boxWidth} onWidth={setBoxWidth} onReturn={() => setBoxed(false)} hostRef={setBoxHost} />}
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

/**
 * The Loop line switch. A page that plays by seeking (loopLineAt) would hold the sound inside its first line for someone
 * who left Loop line on in Review, so there it starts off each visit and neither reads nor writes Review's stored choice.
 */
function useLoopSwitch(byPage: boolean): [boolean, (on: boolean) => void] {
  const [stored, setStored] = usePersistedState('review.loop', false)
  const [visit, setVisit] = useState(false)
  return byPage ? [visit, setVisit] : [stored, setStored]
}

/**
 * Loop line's state, and on a page that plays by seeking (loopLineAt) the line it repeats: while the sound plays with
 * Loop line on and no line was played, the page's line under the playhead is taken as the played line, so it repeats
 * at its end like one. A seek clears it like any played line, and it is taken again where the sound lands. Turning
 * Loop line off drops a line taken only for it, in the same step as the switch, so the sound runs on instead of
 * stopping at that line's end.
 */
function useLoop({ loop, loopRef, playing, loopLineAt, media, segRef, setSeg }: {
  loop: boolean
  loopRef: RefObject<boolean>
  playing: boolean
  loopLineAt: ((time: number) => Segment | null) | undefined
  media: RefObject<HTMLMediaElement | null>
  segRef: RefObject<Segment | null>
  setSeg: (s: Segment | null) => void
}) {
  const taken = useRef<Segment | null>(null)
  useEffect(() => {
    loopRef.current = loop
    if (!loop && taken.current && segRef.current === taken.current) setSeg(null)
    // The refs are the player's own, and setSeg, new each render, only writes them and the segment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loop])
  useEffect(() => {
    if (!loopLineAt || !loop || !playing) return
    let raf = 0
    const look = () => {
      const el = media.current
      const line = el && !segRef.current ? loopLineAt(el.currentTime) : null
      if (line) setSeg((taken.current = line))
      raf = requestAnimationFrame(look)
    }
    raf = requestAnimationFrame(look)
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loopLineAt, loop, playing])
}
