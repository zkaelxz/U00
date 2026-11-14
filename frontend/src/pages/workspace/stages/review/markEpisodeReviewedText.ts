// What Review says after "Mark episode reviewed".
import { plural } from '../../../../labels'
import type { EpisodeReviewedResult } from '../../../../types/review'

export function markEpisodeReviewedText(r: EpisodeReviewedResult): string {
  if (r.reviewed === 0) return 'No lines needed marking.'
  const parts = [`Marked ${plural(r.reviewed, 'line')} reviewed`, `${r.remembered} added to translation memory`]
  if (r.kept_existing) parts.push(`${r.kept_existing} already in memory kept`)
  if (r.skipped_flagged) parts.push(`${plural(r.skipped_flagged, 'flagged line')} left alone`)
  return `${parts.join('; ')}.`
}

// Shown with Mark episode reviewed and with the check queue's Mark the rest as checked, which runs the same call.
export const MARK_REVIEWED_HELP =
  'Use this once you have read the whole episode. Every translated line that is not flagged or already checked is marked ' +
  "checked, and the lines you did not edit are recorded as approved translations in the series' translation memory, " +
  'so they can be offered as Accept suggestions and as examples on later Translate runs. Lines you never read would teach their mistakes too.'
