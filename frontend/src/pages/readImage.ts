/*
 * Pure helpers for "Read from a screenshot" on the Translate page
 * (pages/TranslateReadImage.tsx): which engines take pictures, the crop and
 * zoom arithmetic, and picking an image out of a paste or drop. Kept here so
 * they can be tested without a browser.
 */
import type { TranslateEngine } from '../types/translate'

/** Mirrors engine_backends/vision.py VISION_ENGINES (DeepSeek takes no pictures). */
const VISION_ENGINE_NAMES = ['claude', 'gemini', 'openai', 'ollama']

/** The server rejects anything but these (services/page_import_limits.py); WebP is sent on as PNG. */
const ACCEPTED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
/** Mirrors services/image_text_service.MAX_EDGE: past it the server cuts the picture down. */
export const MAX_EDGE = 2000
export const ZOOM_CHOICES = [1, 2, 3, 4]
/** A selection smaller than this on either side is a stray click, not a crop. */
const MIN_SELECTION_PX = 8

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface CropPlan {
  sx: number
  sy: number
  sw: number
  sh: number
  outW: number
  outH: number
}

export function visionEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return engines.filter((e) => VISION_ENGINE_NAMES.includes(e.name))
}

/**
 * The engine to show: the remembered one while it is offered, else the page's
 * engine when it takes pictures, else the first one that can run (a local model
 * comes first in the list order the server gives), else ''.
 */
export function pickVisionEngine(engines: TranslateEngine[], remembered: string, pageEngine: string): string {
  const offered = visionEngines(engines)
  if (remembered && offered.some((e) => e.name === remembered)) return remembered
  if (offered.some((e) => e.name === pageEngine)) return pageEngine
  return (offered.find((e) => e.key_configured) ?? offered[0])?.name ?? ''
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** The box between two points, as fractions of the picture (each 0..1), whichever corner was dragged first. */
export function rectBetween(a: { x: number; y: number }, b: { x: number; y: number }): Rect {
  const x0 = clamp01(Math.min(a.x, b.x))
  const y0 = clamp01(Math.min(a.y, b.y))
  return { x: x0, y: y0, w: clamp01(Math.max(a.x, b.x)) - x0, h: clamp01(Math.max(a.y, b.y)) - y0 }
}

/**
 * What to cut from a natW x natH picture and how big to draw it: the selection
 * (the whole picture when none, or when it is a stray click) enlarged by `zoom`,
 * then cut back to MAX_EDGE on the long side because the server would anyway.
 */
export function cropPlan(natW: number, natH: number, rect: Rect | null, zoom: number): CropPlan {
  let sx = 0
  let sy = 0
  let sw = natW
  let sh = natH
  if (rect) {
    const w = Math.round(rect.w * natW)
    const h = Math.round(rect.h * natH)
    if (w >= MIN_SELECTION_PX && h >= MIN_SELECTION_PX) {
      sx = Math.min(natW - w, Math.round(rect.x * natW))
      sy = Math.min(natH - h, Math.round(rect.y * natH))
      sw = w
      sh = h
    }
  }
  const scale = Math.min(Math.max(1, zoom), MAX_EDGE / Math.max(sw, sh))
  return { sx, sy, sw, sh, outW: Math.max(1, Math.round(sw * scale)), outH: Math.max(1, Math.round(sh * scale)) }
}

/** The first accepted picture among a paste's or drop's items, else null. */
export function firstImage(items: ArrayLike<{ kind?: string; type: string; getAsFile?: () => File | null }>): File | null {
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i]
    if (item.kind !== undefined && item.kind !== 'file') continue
    if (!ACCEPTED_IMAGE_TYPES.includes(item.type)) continue
    const file = item.getAsFile?.()
    if (file) return file
  }
  return null
}

/** A chosen file or a file list entry that is a picture the server accepts. */
export function isAcceptedImage(file: { type: string }): boolean {
  return ACCEPTED_IMAGE_TYPES.includes(file.type)
}

/** The new text after a read: appended on its own line, so several screenshots add up. */
export function appendRead(current: string, read: string): string {
  const text = read.trim()
  if (!text) return current
  return current.trim() ? `${current.replace(/\s+$/, '')}\n${text}` : text
}
