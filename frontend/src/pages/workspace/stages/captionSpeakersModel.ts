/** Pure parts of the "Read on-screen captions by speaker" panel (CaptionSpeakers.tsx). */
import type { TranslateEngine } from '../../../types/translate'
import { pickVisionEngine, visionEngines } from '../../readImage'

/** Engines that take pictures and can run now. */
export function readyVisionEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return visionEngines(engines).filter((e) => e.key_configured)
}

/** The engine to read with: the remembered one while it can run, else the first that can. '' when none can. */
export function captionEngine(engines: TranslateEngine[], remembered: string): string {
  const ready = readyVisionEngines(engines)
  return ready.length ? pickVisionEngine(ready, remembered, '') : ''
}

/** The reading may start only when the estimate fits what is left of the monthly cap. */
export function canStartReading(estimate: { fits_cap: boolean } | null): boolean {
  return !!estimate && estimate.fits_cap
}

/** The reading's polling interval while it runs. */
export const READING_POLL_MS = 3000
