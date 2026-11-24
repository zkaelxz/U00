import { useEffect, useState } from 'react'

import { clearTopicChapters, generateTopicChapters, getTopicChapters, topicChaptersTextUrl } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { humanize } from '../../../components/labels'
import { browserStorage, writePref } from '../../../hooks/usePersistedState'
import { formatClock } from '../../../labels'
import { routeHref } from '../../../router'
import type { TopicChaptersState } from '../../../types/translateStage'
import { positionKey } from '../../watch/watchLogic'

// Timed topic chapters of a long title. Making them is paid for on a cloud engine, so the engine is named before it
// runs, and the result is saved at once (there is no box to review it in). A chapter opens the Watch page there.
export function TopicChaptersPanel({ dramaId }: { dramaId: number }) {
  const [state, setState] = useState<TopicChaptersState | null>(null)
  const [engine, setEngine] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getTopicChapters(dramaId).then(
      (s) => {
        if (cancelled) return
        setState(s)
        setEngine(s.engines.includes(s.engine) ? s.engine : s.engines[0] ?? '')
      },
      () => undefined, // the rest of Details still works
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (!state || state.engines.length === 0) return null
  const run = (job: () => Promise<{ chapters: TopicChaptersState['chapters'] }>) => {
    setPending(true)
    setError(null)
    job().then((r) => setState({ ...state, chapters: r.chapters }), setError).finally(() => setPending(false))
  }
  return (
    <div data-testid="topic-chapters">
      <div className="actions">
        <select aria-label="Chapter engine" value={engine} onChange={(e) => setEngine(e.target.value)} disabled={pending}>
          {state.engines.map((e) => <option key={e} value={e}>{humanize('engine', e)}</option>)}
        </select>
        <button type="button" onClick={() => run(() => generateTopicChapters(dramaId, engine))} disabled={pending || !engine}>
          {pending ? 'Working…' : state.chapters.length ? 'Make chapters again' : 'Make topic chapters'}
        </button>
        {state.chapters.length > 0 && (
          <>
            <button type="button" onClick={() => run(() => clearTopicChapters(dramaId))} disabled={pending}>Clear</button>
            <a href={topicChaptersTextUrl(dramaId)} download>Timestamp list</a>
          </>
        )}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {state.chapters.length > 0 && (
        <ol>
          {state.chapters.map((c) => (
            <li key={c.start}>
              <a
                href={routeHref({ name: 'watch', id: dramaId })}
                onClick={() => writePref(browserStorage(), positionKey(dramaId), c.start)}
              >
                {formatClock(c.start)} {c.title}
              </a>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
