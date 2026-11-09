// Sizing and placement rules for the in-page floating player.

export const FLOAT_MIN_WIDTH = 220
export const FLOAT_MAX_WIDTH = 960
export const FLOAT_DEFAULT_WIDTH = 360
export const FLOAT_STEP = 40
export const FLOAT_MARGIN = 8

export interface FloatPos {
  // Distance from the viewport's right and bottom edges: the box is anchored there so its height can follow the content.
  right: number
  bottom: number
}

// The viewport is the upper bound so a phone never gets a box wider than the screen.
export function clampFloatWidth(width: number, viewportWidth: number): number {
  const max = Math.max(FLOAT_MIN_WIDTH, Math.min(FLOAT_MAX_WIDTH, viewportWidth - 2 * FLOAT_MARGIN))
  const w = Number.isFinite(width) ? width : FLOAT_DEFAULT_WIDTH
  return Math.round(Math.min(max, Math.max(FLOAT_MIN_WIDTH, w)))
}

// Keeps the box fully on screen; the height is only known after layout, so the caller passes the measured one.
export function clampFloatPos(pos: FloatPos, size: { width: number; height: number }, viewport: { width: number; height: number }): FloatPos {
  const clamp = (v: number, max: number) => Math.round(Math.min(Math.max(0, max), Math.max(0, v)))
  return {
    right: clamp(pos.right, viewport.width - size.width),
    bottom: clamp(pos.bottom, viewport.height - size.height),
  }
}
