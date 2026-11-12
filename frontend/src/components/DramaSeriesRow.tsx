import { useState } from 'react'

import { setDramaSeries } from '../api/library'
import { listAllSeries, type SeriesListItem } from '../api/series'
import type { DramaDetail } from '../api/types'
import { useCancellableEffect } from '../hooks/useCancellableEffect'
import { useHolds } from '../hooks/useHolds'
import { routeHref } from '../router'
import { NEW_SERIES, newSeriesProblem, seriesUpdate } from '../pages/workspace/detailsForm'
import { ErrorBanner } from './ErrorBanner'
import { buttonClass } from './uiClasses'

interface Props {
  drama: DramaDetail
  onChanged: (drama: DramaDetail) => void
}

// Which series a title is in, and the way to file it. A title in no series
// has no glossary, so the popup says so plainly. Writes go through the
// series-only route (library.create), so it works from away too.
export function DramaSeriesRow({ drama, onChanged }: Props) {
  const canEdit = useHolds('library.create')
  const [series, setSeries] = useState<SeriesListItem[] | null>(null)
  const [editing, setEditing] = useState(false)
  const [choice, setChoice] = useState('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)

  useCancellableEffect((cancelled) => {
    listAllSeries().then(
      (r) => !cancelled() && setSeries(r.items),
      () => !cancelled() && setSeries([]), // the picker still offers "+ New series…"
    )
  }, [reloads])

  const current = drama.series_id == null ? '' : String(drama.series_id)
  const name = series?.find((s) => String(s.id) === current)?.name ?? (current ? `Series #${current}` : '')
  const options = series ?? []
  const dirty = choice !== current
  const needsName = choice === NEW_SERIES && !newName.trim()

  const startEditing = () => {
    setChoice(current)
    setNewName('')
    setProblem(null)
    setError(null)
    setEditing(true)
  }

  const save = () => {
    const bad = newSeriesProblem(choice, newName)
    setProblem(bad)
    if (bad) return
    setBusy(true)
    setDramaSeries(drama.id, seriesUpdate(choice, newName)).then(
      (d) => {
        setBusy(false)
        setEditing(false)
        if (choice === NEW_SERIES) setReloads((x) => x + 1)
        onChanged(d)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <div className="drama-series" data-testid="drama-series">
      <div className="drama-series-line">
        <span className="drama-series-label">Series</span>
        {current === '' ? (
          <span>No series <span className="muted">(no shared glossary)</span></span>
        ) : (
          <a href={routeHref({ name: 'series', id: Number(current) })}>{name}</a>
        )}
        {canEdit && !editing && (
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={startEditing}>
            {current === '' ? 'Add to series…' : 'Change…'}
          </button>
        )}
      </div>
      {editing && (
        <div className="drama-series-edit">
          <select aria-label="Series" value={choice} disabled={busy}
            onChange={(e) => { setProblem(null); setChoice(e.target.value) }}>
            <option value="">No series</option>
            {current !== '' && !options.some((s) => String(s.id) === current) && <option value={current}>{name}</option>}
            {options.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
            <option value={NEW_SERIES}>+ New series…</option>
          </select>
          {choice === NEW_SERIES && (
            <input aria-label="New series name" value={newName} maxLength={300} disabled={busy}
              placeholder={(drama.title_en || drama.title_zh || '').trim()}
              onChange={(e) => { setProblem(null); setNewName(e.target.value) }} />
          )}
          {problem && <p className="error" role="alert">{problem}</p>}
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !dirty || needsName} onClick={save}>
              {choice === NEW_SERIES ? 'Create and add' : choice === '' ? 'Remove from series' : 'Save'}
            </button>
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      )}
    </div>
  )
}
