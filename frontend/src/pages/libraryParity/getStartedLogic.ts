export const GET_STARTED_PREF = 'library.getStarted.dismissed'

/** The card is for a library with no dramas, until the user dismisses it. The
 *  count is unknown (null) while stats load or when they fail: show nothing. */
export function showGetStarted(totalDramas: number | null | undefined, dismissed: boolean): boolean {
  return totalDramas === 0 && !dismissed
}
