/** Crop arithmetic for the cover editor: a fixed-ratio box inside a picture. Kept apart so it is testable without a browser. */
export const COVER_RATIO = 2 / 3
/** Widest cover written; a larger crop is scaled down so the upload stays small. */
export const COVER_MAX_WIDTH = 1200

export interface PixelRect {
  x: number
  y: number
  w: number
  h: number
}

/** The largest cover-ratio box that fits the picture. */
export function largestBox(natW: number, natH: number): { w: number; h: number } {
  return natW / natH > COVER_RATIO ? { w: natH * COVER_RATIO, h: natH } : { w: natW, h: natW / COVER_RATIO }
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/**
 * The crop in picture pixels. `size` (0.2..1) is the box's share of the largest box; `px` and `py`
 * (0..1) place it within the room left over, so every pair of values stays inside the picture.
 */
export function cropRect(natW: number, natH: number, size: number, px: number, py: number): PixelRect {
  const max = largestBox(natW, natH)
  const s = Math.min(1, Math.max(0.2, size))
  const w = Math.max(1, Math.round(max.w * s))
  const h = Math.max(1, Math.round(w / COVER_RATIO))
  return {
    x: Math.round(clamp01(px) * Math.max(0, natW - w)),
    y: Math.round(clamp01(py) * Math.max(0, natH - h)),
    w,
    h: Math.min(h, natH),
  }
}

/** The size the crop is written at. */
export function outputSize(rect: PixelRect): { w: number; h: number } {
  const w = Math.min(rect.w, COVER_MAX_WIDTH)
  return { w, h: Math.max(1, Math.round((rect.h * w) / rect.w)) }
}
