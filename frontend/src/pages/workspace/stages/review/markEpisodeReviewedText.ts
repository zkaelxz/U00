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
