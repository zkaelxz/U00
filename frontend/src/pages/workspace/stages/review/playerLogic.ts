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
