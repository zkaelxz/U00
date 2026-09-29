// Pure comic-viewer helpers: per-drama view prefs, key and tap mapping,
// preloading, overlay maths and zoom maths. No React here so it is unit-tested.

import type { StorageLike } from '../../components/sectionStorage'
import { readPref, writePref } from '../../hooks/usePersistedState'
import type { ComicRegion, ComicRegionsResponse } from '../../types/comic'

export const COMIC_MEDIA_TYPES = ['manga', 'manhua', 'manhwa'] as const

export function isComicType(mediaType: string | null | undefined): boolean {
  return !!mediaType && (COMIC_MEDIA_TYPES as readonly string[]).includes(mediaType.toLowerCase())
}

export type ComicMode = 'vertical' | 'paged'
export type ComicFit = 'width' | 'height' | 'original'

export interface ComicPrefs {
  mode: ComicMode
  // Paged only: right-to-left (the left side and ArrowLeft go forward).
  rtl: boolean
  fit: ComicFit
  // Show the typeset (rendered) image where a page has one.
  typeset: boolean
  // Draw the text boxes and list the page's lines.
  text: boolean
}

export const MODE_OPTIONS: [ComicMode, string][] = [
  ['vertical', 'Vertical scroll'],
  ['paged', 'One page at a time'],
]
export const FIT_OPTIONS: [ComicFit, string][] = [
  ['width', 'Fit width'],
  ['height', 'Fit screen height'],
  ['original', 'Original size'],
]

// Manga reads one page at a time, right to left; manhua and manhwa scroll.
export function defaultPrefs(mediaType: string | null | undefined, readingModeDefault?: string | null): ComicPrefs {
  const type = (mediaType ?? '').toLowerCase()
  const mode: ComicMode =
    readingModeDefault === 'vertical' || readingModeDefault === 'paged'
      ? readingModeDefault
      : type === 'manga'
        ? 'paged'
        : 'vertical'
  return { mode, rtl: type === 'manga', fit: mode === 'paged' ? 'height' : 'width', typeset: true, text: false }
}

function pick<T>(v: unknown, options: [T, string][], fallback: T): T {
  return options.some(([o]) => o === v) ? (v as T) : fallback
}

/** Stored prefs merged over the drama's defaults; bad or missing fields fall back. */
export function sanitizePrefs(raw: unknown, defaults: ComicPrefs): ComicPrefs {
  const r = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const bool = (v: unknown, d: boolean) => (typeof v === 'boolean' ? v : d)
  return {
    mode: pick(r.mode, MODE_OPTIONS, defaults.mode),
    rtl: bool(r.rtl, defaults.rtl),
    fit: pick(r.fit, FIT_OPTIONS, defaults.fit),
    typeset: bool(r.typeset, defaults.typeset),
    text: bool(r.text, defaults.text),
  }
}

// Remembered per drama in localStorage ("baihe.pref.comic.view.<id>").
export const prefsKey = (dramaId: number) => `comic.view.${dramaId}`

export function loadComicPrefs(storage: StorageLike | null, dramaId: number, defaults: ComicPrefs): ComicPrefs {
  return sanitizePrefs(readPref<Record<string, unknown>>(storage, prefsKey(dramaId), {}), defaults)
}

export function saveComicPrefs(storage: StorageLike | null, dramaId: number, prefs: ComicPrefs): boolean {
  return writePref(storage, prefsKey(dramaId), prefs)
}

export function comicHref(dramaId: number, page: number | null): string {
  return `#/comic/${dramaId}${page ? `?page=${page}` : ''}`
}

export function clampPage(n: number, count: number): number {
  if (!Number.isFinite(n)) return 1
  return Math.min(Math.max(1, Math.round(n)), Math.max(1, count))
}

export type ComicAction = 'next' | 'prev' | 'first' | 'last' | 'zoomIn' | 'zoomOut' | 'zoomReset'

/**
 * A normalised key combo (hooks/useShortcut comboOf) -> what it does. In
 * vertical mode the up/down keys, Space and PgUp/PgDn are left to the browser
 * so they scroll; the left/right arrows jump a page there.
 */
export function keyAction(combo: string, mode: ComicMode, rtl: boolean): ComicAction | null {
  const flip = mode === 'paged' && rtl
  switch (combo) {
    case 'arrowright':
      return flip ? 'prev' : 'next'
    case 'arrowleft':
      return flip ? 'next' : 'prev'
    case 'home':
      return 'first'
    case 'end':
      return 'last'
    case '+':
    case '=':
      return 'zoomIn'
    case '-':
      return 'zoomOut'
    case '0':
      return 'zoomReset'
  }
  if (mode !== 'paged') return null
  if (combo === 'arrowdown' || combo === 'pagedown' || combo === ' ') return 'next'
  if (combo === 'arrowup' || combo === 'pageup' || combo === 'shift+ ') return 'prev'
  return null
}

export type TapAction = 'next' | 'prev' | 'chrome'

/** A tap at `fraction` (0..1) across the page: thirds; the middle shows or hides the bars. */
export function tapAction(fraction: number, rtl: boolean): TapAction {
  if (fraction < 1 / 3) return rtl ? 'next' : 'prev'
  if (fraction > 2 / 3) return rtl ? 'prev' : 'next'
  return 'chrome'
}

/** 1-based pages to fetch ahead: the next 3 when scrolling, the next and previous when paged. */
export function preloadWindow(current: number, count: number, mode: ComicMode): number[] {
  const want = mode === 'vertical' ? [current + 1, current + 2, current + 3] : [current + 1, current - 1]
  return want.filter((p) => p >= 1 && p <= count)
}

/** 1-based pages whose text boxes are needed now. */
export function textPages(current: number, count: number, mode: ComicMode): number[] {
  const want = mode === 'vertical' ? [current, current + 1, current - 1] : [current]
  return want.filter((p) => p >= 1 && p <= count)
}

// Text boxes for one page, or why they could not be loaded.
export type RegionsState = ComicRegionsResponse | { error: unknown }

export function isRegionsError(s: RegionsState | undefined | 'none'): s is { error: unknown } {
  return !!s && s !== 'none' && 'error' in s
}

const pct = (v: number) => Math.round(v * 10000) / 100

/**
 * A text box (original-image pixels) as percentages of the page, clamped to
 * the page, so it lines up at any displayed size. Null when the page size is
 * unknown or the box is empty.
 */
export function regionBox(
  r: Pick<ComicRegion, 'x' | 'y' | 'w' | 'h'>,
  width: number | null,
  height: number | null,
): { left: number; top: number; width: number; height: number } | null {
  if (!width || !height || width <= 0 || height <= 0) return null
  const x0 = Math.min(Math.max(r.x, 0), width)
  const y0 = Math.min(Math.max(r.y, 0), height)
  const x1 = Math.min(Math.max(r.x + r.w, 0), width)
  const y1 = Math.min(Math.max(r.y + r.h, 0), height)
  if (x1 <= x0 || y1 <= y0) return null
  return { left: pct(x0 / width), top: pct(y0 / height), width: pct((x1 - x0) / width), height: pct((y1 - y0) / height) }
}

/** Reading order, and the text to show for a box (translation, else the source). */
export function orderedLines(regions: ComicRegion[]): { idx: number; text: string; source: string | null; kind: string | null }[] {
  return [...regions]
    .sort((a, b) => a.idx - b.idx)
    .map((r) => ({ idx: r.idx, text: (r.translated_text ?? '').trim() || (r.source_text ?? '').trim(), source: r.source_text, kind: r.kind }))
    .filter((l) => l.text)
}

// ---- Zoom: transform = translate(x, y) scale(scale), origin top left ----

export interface Zoom {
  scale: number
  x: number
  y: number
}

export const NO_ZOOM: Zoom = { scale: 1, x: 0, y: 0 }
export const ZOOM = { min: 1, max: 4, double: 2, step: 1.25 }

/** Keep the zoomed content covering its box (w x h): no gaps at the edges. */
export function clampZoom(z: Zoom, w: number, h: number): Zoom {
  const scale = Math.min(ZOOM.max, Math.max(ZOOM.min, z.scale))
  if (scale <= ZOOM.min + 1e-3) return NO_ZOOM
  const clamp = (v: number, size: number) => Math.min(0, Math.max(size - scale * size, v))
  return { scale, x: clamp(z.x, w), y: clamp(z.y, h) }
}

/** Scale by `factor` keeping the point (cx, cy) (box coordinates) still. */
export function zoomAt(z: Zoom, factor: number, cx: number, cy: number, w: number, h: number): Zoom {
  const scale = Math.min(ZOOM.max, Math.max(ZOOM.min, z.scale * factor))
  const px = (cx - z.x) / z.scale
  const py = (cy - z.y) / z.scale
  return clampZoom({ scale, x: cx - scale * px, y: cy - scale * py }, w, h)
}

/** Double-tap: 2x around the tap, or back to 1x when already zoomed. */
export function toggleZoomAt(z: Zoom, cx: number, cy: number, w: number, h: number): Zoom {
  return z.scale > 1 ? NO_ZOOM : zoomAt(NO_ZOOM, ZOOM.double, cx, cy, w, h)
}
