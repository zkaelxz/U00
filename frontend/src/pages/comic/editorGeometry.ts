// Pure geometry for the comic editor: boxes are in page pixels, the screen
// only enters through one scale (displayed width / page width).

export interface Box {
  x: number
  y: number
  w: number
  h: number
}
export interface PageSize {
  width: number
  height: number
}
export type Corner = 'nw' | 'ne' | 'sw' | 'se'

// The backend refuses a box under 1 px; a few more keeps a handle grabbable.
export const MIN_BOX = 8

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi)

export function screenToPage(delta: number, scale: number): number {
  return scale > 0 ? delta / scale : 0
}
export function pageToScreen(value: number, scale: number): number {
  return value * scale
}

/** Whole pixels, as the server stores them, with the box kept inside the page. */
export function clampBox(b: Box, page: PageSize): Box {
  const w = clamp(Math.round(b.w), Math.min(MIN_BOX, page.width), page.width)
  const h = clamp(Math.round(b.h), Math.min(MIN_BOX, page.height), page.height)
  return { x: clamp(Math.round(b.x), 0, page.width - w), y: clamp(Math.round(b.y), 0, page.height - h), w, h }
}

export function moveBox(b: Box, dx: number, dy: number, page: PageSize): Box {
  return clampBox({ ...b, x: b.x + dx, y: b.y + dy }, page)
}

/** The corner follows the pointer; the opposite corner stays put. */
export function resizeBox(b: Box, corner: Corner, dx: number, dy: number, page: PageSize): Box {
  const minW = Math.min(MIN_BOX, page.width)
  const minH = Math.min(MIN_BOX, page.height)
  let left = b.x
  let top = b.y
  let right = b.x + b.w
  let bottom = b.y + b.h
  if (corner === 'nw' || corner === 'sw') left = clamp(left + dx, 0, right - minW)
  else right = clamp(right + dx, left + minW, page.width)
  if (corner === 'nw' || corner === 'ne') top = clamp(top + dy, 0, bottom - minH)
  else bottom = clamp(bottom + dy, top + minH, page.height)
  return clampBox({ x: left, y: top, w: right - left, h: bottom - top }, page)
}

export function sameBox(a: Box, b: Box): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h
}
