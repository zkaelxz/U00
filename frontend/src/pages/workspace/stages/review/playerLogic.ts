// Pure helpers for the Review player: typed jump times, the subtitle track
// URL (the Reader's caption route, built from the current lines) and the line
// under the playhead.

import { captionUrl } from '../../../../api/reader'
import type { CaptionTrack } from '../../../../types/reader'
import type { ReviewLine } from '../../../../types/review'

export type SubtitleChoice = CaptionTrack | 'off'

export const SUBTITLE_OPTIONS: { value: SubtitleChoice; label: string }[] = [
  { value: 'English', label: 'English' },
  { value: 'Source', label: 'Original' },
  { value: 'Bilingual', label: 'Both' },
  { value: 'off', label: 'Off' },
]

// Slow speeds help to time overlapping speakers; every one keeps the pitch.
export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]

// A remembered rate that is no longer on the list falls back to normal speed.
export function validRate(rate: number): number {
  return PLAYBACK_RATES.includes(rate) ? rate : 1
}

export const JUMP_ERROR = 'Type a time like 1:23, 1:02:03 or 83.5.'

// "83.5", "1:23", "1:02:03" (and "1:23.5") -> seconds; null when unreadable.
export function parseJumpTime(text: string): number | null {
  const t = text.trim()
  if (!t) return null
  const parts = t.split(':')
  if (parts.length > 3) return null
  if (!parts.every((p, i) => (i === parts.length - 1 ? /^\d+(\.\d+)?$/ : /^\d+$/).test(p))) return null
  const nums = parts.map(Number)
  // Minutes and seconds after the first field stay under 60.
  if (nums.slice(1).some((n) => n >= 60)) return null
  return nums.reduce((acc, n) => acc * 60 + n, 0)
}

// Clamp into [0, duration] when the duration is known.
export function clampTime(seconds: number, duration: number): number {
  const lo = Math.max(0, seconds)
  return Number.isFinite(duration) && duration > 0 ? Math.min(lo, duration) : lo
}

// The subtitle track's URL; `version` changes whenever lines are saved, so the
// browser fetches the edited text instead of reusing the old cues.
export function subtitleSrc(dramaId: number, choice: SubtitleChoice, version: number): string | null {
  if (choice === 'off') return null
  return `${captionUrl(dramaId, choice)}?v=${version}`
}

// The line playing at `time` (start inclusive, end exclusive), from the lines on screen.
export function lineAt<T extends Pick<ReviewLine, 'start' | 'end'>>(lines: T[], time: number): T | null {
  return lines.find((l) => time >= l.start && time < l.end) ?? null
}

export type CaptionSize = 'smaller' | 'default' | 'larger'

export const CAPTION_SIZE_OPTIONS: { value: CaptionSize; label: string }[] = [
  { value: 'smaller', label: 'Smaller' },
  { value: 'default', label: 'Default' },
  { value: 'larger', label: 'Larger' },
]

const CAPTION_FACTOR: Record<CaptionSize, number> = { smaller: 0.8, default: 1, larger: 1.35 }
const CAPTION_MIN_PX = 16
const CAPTION_MAX_PX = 72

// Pop-out caption font size from the window's width: about 2.25% of it, so a
// 2000px window reads from across a desk, never below the inline caption's size
// and capped so an ultrawide window doesn't turn one line into a banner.
export function popoutCaptionPx(windowWidth: number, size: CaptionSize = 'default'): number {
  if (!Number.isFinite(windowWidth) || windowWidth <= 0) return CAPTION_MIN_PX
  const px = windowWidth * 0.0225 * CAPTION_FACTOR[size]
  return Math.round(Math.min(CAPTION_MAX_PX, Math.max(CAPTION_MIN_PX, px)))
}

export const SEEK_STEP_S = 5
export const SEEK_STEP_FINE_S = 1

export interface PlayerKey {
  key: string
  shiftKey: boolean
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
  target: { closest: (selector: string) => unknown } | null
}

export type PlayerKeyAction = { kind: 'seek'; to: number } | { kind: 'playpause' } | { kind: 'fullscreen' } | null

// Controls that use the arrow keys (or typed letters) themselves: the seek
// bar, the selects, the jump field and the waveform handles.
const OWN_KEYS = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="slider"], [role="spinbutton"], [role="textbox"], [role="combobox"], [role="listbox"]'
// Space on these is their own click, so it must not also toggle playback.
const CLICKABLE = 'button, a[href], summary'

// What a key pressed inside the player's frame does. Review's line shortcuts
// (J/K, F to dismiss a flag, Space to play a line) live outside this frame, so
// these keys only apply while the player itself has focus.
export function playerKeyAction(e: PlayerKey, time: number, duration: number): PlayerKeyAction {
  if (e.ctrlKey || e.metaKey || e.altKey) return null
  if (e.target?.closest(OWN_KEYS)) return null
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    const step = (e.shiftKey ? SEEK_STEP_FINE_S : SEEK_STEP_S) * (e.key === 'ArrowLeft' ? -1 : 1)
    return { kind: 'seek', to: clampTime(time + step, duration) }
  }
  if (e.shiftKey) return null
  if (e.key === 'f' || e.key === 'F') return { kind: 'fullscreen' }
  if (e.key === ' ' && !e.target?.closest(CLICKABLE)) return { kind: 'playpause' }
  return null
}

// The cue as drawn over the picture: no blank lines, and nothing at all for an
// empty cue so no empty box is drawn.
export function overlayText(cue: string): string {
  return cue
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .join('\n')
}

// Controls over a full-screen picture hide after this long without activity.
export const FULLSCREEN_IDLE_MS = 3000
