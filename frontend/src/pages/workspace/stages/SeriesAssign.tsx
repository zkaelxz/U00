import { useEffect, useState } from 'react'

import { getSeries, updateDramaMetadata } from '../../../api/library'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import type { LibrarySeries } from '../../../types/library'
import { NEW_SERIES, newSeriesProblem, seriesUpdate } from '../detailsForm'
import { useStage } from '../StageContext'
import './seriesAssign.css'

// Parity X09: the series picker inside the glossary box. Glossary terms
// belong to a series, so a drama with none gets a one-tap "Create series"
// (named after the drama) plus the same picker Edit details uses; both
// write through POST /api/dramas/{id}/metadata.
export function SeriesAssign() {
  const { dramaId, drama, refetchDrama } = useStage()
  const current = drama.series_id == null ? '' : String(drama.series_id)
  const [series, setSeries] = useState<LibrarySeries[] | null>(null)
  const [choice, setChoice] = useState(current)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)

  // Follow the drama when its series changes elsewhere (Edit details).
  const [seenCurrent, setSeenCurrent] = useState(current)
  if (seenCurrent !== current) {
    setSeenCurrent(current)
    setChoice(current)
  }

  useEffect(() => {
    let cancelled = false
    getSeries().then(
      (r) => !cancelled && setSeries(r.items),
      () => !cancelled && setSeries([]), // the picker still offers "+ New series…"
    )
    return () => {
      cancelled = true
    }
  }, [reloads])

  const title = (drama.title_en || drama.title_zh || '').trim()
  const name = series?.find((s) => String(s.id) === current)?.name ?? (current ? `Series #${current}` : '')

  const save = (c: string, n: string) => {
    const bad = newSeriesProblem(c, n)
    setProblem(bad)
    if (bad) return
    setBusy(true)
    updateDramaMetadata(dramaId, seriesUpdate(c, n)).then(
      () => {
        setBusy(false)
        setError(null)
        setNewName('')
        if (c === NEW_SERIES) setReloads((x) => x + 1)
        refetchDrama()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const options = series ?? []
  const listed = current === '' || options.some((s) => String(s.id) === current)
  const dirty = choice !== current
  const reason = !dirty ? null : choice === NEW_SERIES && !newName.trim() ? 'Still needed: a name for the new series.' : null

  return (
    <div className="series-assign" data-testid="series-assign">
      {current === '' ? (
        <div className="series-assign-empty" role="note">
          <p>
            <strong>This title isn't in a series, so it can't hold glossary terms.</strong>{' '}
            Terms belong to a series and are shared by every title in it.
          </p>
          {title && (
            <button type="button" className={buttonClass('primary', 'sm')} disabled={busy} onClick={() => save(NEW_SERIES, title)}>
              Create series “{title}”
            </button>
          )}
        </div>
      ) : (
        <p className="muted" data-testid="series-assign-current">
          Terms are shared by every title in <strong>{name}</strong>.
        </p>
      )}
      <div className="series-assign-row">
        <Field label="Series" help="Changing it here is the same as Series in Edit details (Media stage).">
          <select value={choice} disabled={busy} onChange={(e) => { setProblem(null); setChoice(e.target.value) }}>
            <option value="">No series</option>
            {!listed && <option value={current}>{name}</option>}
            {options.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
            <option value={NEW_SERIES}>+ New series…</option>
          </select>
        </Field>
        {choice === NEW_SERIES && (
          <Field label="New series name" error={problem ?? undefined}>
            <input value={newName} maxLength={300} placeholder={title} onChange={(e) => { setProblem(null); setNewName(e.target.value) }} />
          </Field>
        )}
      </div>
      {dirty && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !!reason} onClick={() => save(choice, newName)}>
            {choice === NEW_SERIES ? 'Create and assign' : choice === '' ? 'Remove from series' : 'Move to this series'}
          </button>
          {reason && <span className="muted">{reason}</span>}
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
