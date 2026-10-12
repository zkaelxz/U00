// A label inside a waveform region is drawn only when it fits on one line: a
// clipped "#4" with the "0" wrapped under it reads as a different line.
const CHAR_PX = 7
const PAD_PX = 8

export function labelFits(text: string, widthPx: number): boolean {
  return Number.isFinite(widthPx) && widthPx >= text.length * CHAR_PX + PAD_PX
}
