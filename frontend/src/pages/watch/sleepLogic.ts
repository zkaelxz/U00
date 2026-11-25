// The Watch page's sleep timer: the choices offered and the countdown text.

export const SLEEP_MINUTES = [15, 30, 45, 60] as const

/** Time left on a timer ending at `endAt`, never below zero. */
export const sleepRemainingMs = (endAt: number, now: number) => Math.max(0, endAt - now)

/** "12 min left" while minutes remain, "Under a minute left" for the last one. */
export function sleepLeftText(remainingMs: number): string {
  const minutes = Math.ceil(remainingMs / 60000)
  return minutes <= 1 ? 'Under a minute left' : `${minutes} min left`
}
