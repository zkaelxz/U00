// The series page's shared glossary: join one group of terms several series inherit, and see where each term comes from.
import { useCallback, useState } from 'react'

import type { SeriesPageData } from '../../api/series'
import {
  createSharedGroup, deleteSharedTerm, getSeriesSharedGlossary, listSharedGroups, originLabel, saveSharedTerm,
  setSeriesSharedGroup,
} from '../../api/sharedGlossary'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { useLoad } from '../../hooks/useLoad'

export function SharedGlossaryCard({ data, onChanged }: { data: SeriesPageData; onChanged: () => void }) {
  const [reloadKey, setReloadKey] = useState(0)
  const load = useCallback(() => getSeriesSharedGlossary(data.id), [data.id])
  const view = useLoad(load, reloadKey)
  const groups = useLoad(listSharedGroups, reloadKey)
  const [pick, setPick] = useState('')
  const [newName, setNewName] = useState('')
  const [original, setOriginal] = useState('')
  const [translation, setTranslation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const run = (p: Promise<unknown>, done?: () => void) => {
    setBusy(true)
    setError(null)
    p.then(
      () => { setBusy(false); setReloadKey((k) => k + 1); onChanged(); done?.() },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const current = view.data
  const group = current?.group ?? null
  const canEdit = data.can_edit && !!current?.can_edit
  const others = (groups.data?.items ?? []).filter((g) => g.id !== group?.id)

  return (
    <Card title="Shared glossary">
      <p className="muted">
        A shared glossary carries slang, catchphrases and ASR mishearings to every series that joins it, for example one
        per creator. This series' own terms win over a shared term with the same original text. Leaving keeps this
        series' own terms.
      </p>
      <ErrorBanner error={view.error ?? groups.error} />
      {current && (
        <p data-testid="series-shared-group">
          {group
            ? <>Using <strong>{group.name}</strong> ({group.term_count} shared terms, {group.series_count} series)</>
            : 'Not using a shared glossary.'}
        </p>
      )}
      {canEdit && (
        <>
          <div className="series-copy-row">
            <Field label={group ? 'Switch to another shared glossary' : 'Join a shared glossary'}>
              <select value={pick} disabled={busy} onChange={(e) => setPick(e.target.value)}>
                <option value="">Pick one…</option>
                {others.map((g) => <option key={g.id} value={String(g.id)}>{g.name} ({g.term_count})</option>)}
              </select>
            </Field>
            <div className="actions">
              <button
                type="button"
                className={buttonClass('secondary')}
                disabled={busy || !pick}
                onClick={() => run(setSeriesSharedGroup(data.id, Number(pick)), () => setPick(''))}
              >
                Join
              </button>
              {group && (
                <button
                  type="button"
                  className={buttonClass('ghost')}
                  disabled={busy}
                  onClick={() => run(setSeriesSharedGroup(data.id, null))}
                >
                  Leave
                </button>
              )}
            </div>
          </div>
          <form
            className="series-copy-row"
            onSubmit={(e) => {
              e.preventDefault()
              if (!newName.trim()) return
              run(createSharedGroup(newName.trim()).then((g) => setSeriesSharedGroup(data.id, g.id)), () => setNewName(''))
            }}
          >
            <Field label="New shared glossary (this series joins it)">
              <input
                value={newName}
                maxLength={300}
                disabled={busy}
                placeholder="e.g. the creator's name"
                onChange={(e) => setNewName(e.target.value)}
              />
            </Field>
            <div className="actions">
              <button type="submit" className={buttonClass('secondary')} disabled={busy || !newName.trim()}>Create</button>
            </div>
          </form>
        </>
      )}
      {current && group && current.terms.length > 0 && (
        <ul className="series-titles" data-testid="series-effective-terms">
          {current.terms.map((t) => (
            <li key={t.id} className="series-title-row">
              <span className="series-title-text">{t.term_original} → {t.term_translation}</span>
              <span className="series-title-meta">
                <Badge>{originLabel(t)}</Badge>
                {t.origin === 'shared' && group.can_edit && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className={buttonClass('ghost', 'sm')}
                      disabled={busy}
                      onClick={() => run(deleteSharedTerm(group.id, -t.id))}
                    >
                      Remove
                    </button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {group?.can_edit && (
        <form
          className="series-copy-row"
          onSubmit={(e) => {
            e.preventDefault()
            if (!original.trim() || !translation.trim()) return
            run(saveSharedTerm(group.id, original.trim(), translation.trim()), () => { setOriginal(''); setTranslation('') })
          }}
        >
          <Field label="Add a shared term">
            <div className="actions">
              <input value={original} disabled={busy} placeholder="Original" onChange={(e) => setOriginal(e.target.value)} />
              <input value={translation} disabled={busy} placeholder="Translation" onChange={(e) => setTranslation(e.target.value)} />
            </div>
          </Field>
          <div className="actions">
            <button type="submit" className={buttonClass('secondary')} disabled={busy || !original.trim() || !translation.trim()}>
              Add
            </button>
          </div>
        </form>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Card>
  )
}
