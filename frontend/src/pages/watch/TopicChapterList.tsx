import { useEffect, useState } from 'react'

import { getTopicChapters } from '../../api/translateStage'
import { formatClock } from '../../labels'
import type { TopicChapter } from '../../types/translateStage'

/** The title's topic chapters, each a button that plays from its start. Nothing is drawn for a title with none. */
export function TopicChapterList({ dramaId, at, onPick }: { dramaId: number; at: number | null; onPick: (start: number) => void }) {
  const [chapters, setChapters] = useState<TopicChapter[]>([])

  useEffect(() => {
    let cancelled = false
    getTopicChapters(dramaId).then((s) => !cancelled && setChapters(s.chapters), () => undefined) // chapters are optional
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (chapters.length === 0) return null
  const here = at === null ? -1 : chapters.reduce((n, c, i) => (c.start <= at ? i : n), -1)
  return (
    <section className="card" aria-labelledby="watch-chapters-title" data-testid="watch-chapters">
      <h2 className="card-title" id="watch-chapters-title">Chapters</h2>
      <ol className="watch-chapters">
        {chapters.map((c, i) => (
          <li key={c.start}>
            <button type="button" aria-current={i === here ? 'true' : undefined} onClick={() => onPick(c.start)}>
              <span className="muted">{formatClock(c.start)}</span> {c.title}
            </button>
          </li>
        ))}
      </ol>
    </section>
  )
}
