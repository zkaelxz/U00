import { useCallback, useEffect, useImperativeHandle, useRef, useState, type ReactNode, type Ref, type SyntheticEvent } from 'react'

import { mediaStreamUrl, type MediaKind } from '../../../../api/media'
import { usePersistedState } from '../../../../hooks/usePersistedState'
import type { ReviewLine } from '../../../../types/review'
import { formatDuration, formatTime } from './reviewLogic'

export interface PlayerHandle {
  // Seek to the line's start and stop at its end (or repeat it with Loop line).
  playLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  // Play or stop the given line: stops when that line is already playing.
  toggleLine: (line: Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>) => void
  togglePlay: () => void
  toggleLoop: () => void
}

type Segment = Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>

interface Props {
  dramaId: number
  kind: MediaKind
  ref?: Ref<PlayerHandle>
  // Extra controls at the end of the strip (the pager, on phones).
  trailing?: ReactNode
}

export const PLAY_ERROR = 'Couldn’t play the audio. Check the file on Source.'

// A compact player strip over the Slice 52 Range endpoint. Only rendered when
// the drama has audio (or a source video); the element fetches the stream itself.
export function Player({ dramaId, kind, ref, trailing }: Props) {
  const media = useRef<HTMLMediaElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(NaN)
  const [segment, setSegment] = useState<Segment | null>(null)
  const [failed, setFailed] = useState(false)
  const [loop, setLoop] = usePersistedState('review.loop', false)
  const [showVideo, setShowVideo] = usePersistedState('review.video', false)
  const loopRef = useRef(loop)
  const segRef = useRef<Segment | null>(null)

  useEffect(() => {
    loopRef.current = loop
  }, [loop])

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

  const play = useCallback(() => {
    const el = media.current
    if (!el) return
    el.play().catch(() => setFailed(true))
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

  return (
    <div className="review-player" role="group" aria-label="Player">
      <div className="review-player-row">
        <button
          type="button"
          className="review-play"
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
        {segment && <span className="muted">Line #{segment.idx}</span>}
        <label className="review-check" title="Repeat the line being played (L)">
          <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> Loop<span className="review-loop-word"> line</span>
        </label>
        {kind === 'video' && (
          <button type="button" className="link" aria-expanded={showVideo} onClick={() => setShowVideo(!showVideo)}>
            {showVideo ? 'Hide video' : 'Show video'}
          </button>
        )}
        {trailing}
      </div>
      {failed && (
        <p className="error" role="alert">
          {PLAY_ERROR}
        </p>
      )}
      {kind === 'video' ? (
        // Kept mounted while hidden, so its sound still plays with the video folded away.
        <video
          ref={(el) => {
            media.current = el
          }}
          className="review-video"
          hidden={!showVideo}
          playsInline
          {...common}
        />
      ) : (
        <audio
          ref={(el) => {
            media.current = el
          }}
          {...common}
        />
      )}
    </div>
  )
}
