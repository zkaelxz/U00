/** The bar over a TV-mode Live view: Exit, where the picture goes and the original line. It hides itself after a few seconds. */
import { useEffect, useRef, type KeyboardEvent } from 'react'

import { buttonClass } from '../../components/uiClasses'
import type { TvVideo } from './useTvMode'
import type { TvTextSize } from './tvSize'
import { TV_SIZES, TV_SPLITS, type TvSplit } from './tvSize'

const PLACES: { value: TvVideo; label: string }[] = [
  { value: 'side', label: 'Beside' },
  { value: 'above', label: 'Above' },
  { value: 'off', label: 'No video' },
]

type Props = {
  shown: boolean
  /** Whether a picture could be shown, which is when its place means anything. */
  hasPicture: boolean
  video: TvVideo
  onVideo: (v: TvVideo) => void
  showOriginal: boolean
  onShowOriginal: (v: boolean) => void
  split: TvSplit
  onSplit: (s: TvSplit) => void
  textSize: TvTextSize
  onTextSize: (s: TvTextSize) => void
  onExit: () => void
}

export function TvBar({ shown, hasPicture, video, onVideo, showOriginal, onShowOriginal, split, onSplit, textSize, onTextSize, onExit }: Props) {
  const bar = useRef<HTMLDivElement>(null)
  // A remote's arrow keys need a focused control to start from.
  useEffect(() => {
    if (shown && !bar.current?.contains(document.activeElement)) bar.current?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true })
  }, [shown])
  // Left and right walk the buttons: a remote has no pointer and may have no spatial navigation.
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const items = Array.from(bar.current?.querySelectorAll<HTMLElement>('button') ?? [])
    const at = items.indexOf(document.activeElement as HTMLElement)
    if (at < 0) return
    e.preventDefault()
    items[(at + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length]?.focus()
  }
  return (
    <div ref={bar} className="live-tv-bar" data-shown={shown || undefined} aria-hidden={!shown || undefined} inert={!shown || undefined} onKeyDown={onKeyDown}>
      <button type="button" className={buttonClass('primary')} onClick={onExit}>Exit TV mode</button>
      {hasPicture && (
        <div className="live-sizes" role="radiogroup" aria-label="Video">
          {PLACES.map((p) => (
            <button key={p.value} type="button" role="radio" aria-checked={video === p.value}
              className={buttonClass(video === p.value ? 'primary' : 'secondary')} onClick={() => onVideo(p.value)}>
              {p.label}
            </button>
          ))}
        </div>
      )}
      {hasPicture && video === 'side' && (
        <div className="live-sizes" role="radiogroup" aria-label="Picture width">
          {TV_SPLITS.map((s) => (
            <button key={s.value} type="button" role="radio" aria-checked={split === s.value}
              className={buttonClass(split === s.value ? 'primary' : 'secondary')} onClick={() => onSplit(s.value)}>
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="live-sizes" role="radiogroup" aria-label="Text size">
        {TV_SIZES.map((z) => (
          <button key={z.value} type="button" role="radio" aria-checked={textSize === z.value} aria-label={z.name}
            className={buttonClass(textSize === z.value ? 'primary' : 'secondary')} onClick={() => onTextSize(z.value)}>
            {z.label}
          </button>
        ))}
      </div>
      <button type="button" role="switch" aria-checked={showOriginal} className={buttonClass(showOriginal ? 'primary' : 'secondary')} onClick={() => onShowOriginal(!showOriginal)}>
        Original
      </button>
      <p className="live-tv-hint">Esc or Back leaves TV mode.</p>
    </div>
  )
}
