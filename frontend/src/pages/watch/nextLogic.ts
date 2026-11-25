// What the Watch page's Next card shows and when it queues the next title.
import type { NextEpisode } from '../../types/nextEpisode'

/** A free engine starts by itself near the end of an episode; a paid one waits for a tap. */
export const shouldPrepareNext = (next: NextEpisode, canStart: boolean) => canStart && next.state === 'idle' && next.free

/** The card's status line, or null while it stays hidden: a title that is ready shows early, any other only at the end. */
export function nextCardText(next: NextEpisode | null, ended: boolean): string | null {
  if (!next || next.drama_id === null) return null
  if (next.state === 'ready') return 'Ready to watch'
  if (!ended) return null
  return next.state === 'working' ? 'Being translated, it will be ready soon' : 'Not translated yet'
}
