import { useEffect, useState } from 'react'

import {
  acceptTm,
  deleteNote,
  listHistory,
  listNotes,
  listTmSuggestions,
  listVersions,
} from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import type { HistoryItem, ReviewNote, TmSuggestion, VersionItem } from '../../../../types/review'

interface Records {
  notes: ReviewNote[]
  versions: VersionItem[]
  history: HistoryItem[]
  tm: TmSuggestion[]
}

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
}

// Notes (add lives on each line), translation versions and history (read-only;
// restore is a later slice) and translation-memory suggestions.
export function RecordsPanel({ dramaId, reloads, onChanged }: Props) {
  const [records, setRecords] = useState<Records | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      listNotes(dramaId),
      listVersions(dramaId),
      listHistory(dramaId),
      listTmSuggestions(dramaId),
    ]).then(
      ([notes, versions, history, tm]) => {
        if (cancelled) return
        setError(null)
        setRecords({ notes, versions, history, tm })
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const act = (p: Promise<unknown>) =>
    p.then(() => {
      setError(null)
      onChanged()
    }, setError)

  return (
    <section className="panel" aria-label="Records">
      <h3>Records</h3>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {records && (
        <>
          <h4>Notes</h4>
          <ul data-testid="notes-list">
            {records.notes.length === 0 && <li className="muted">No notes yet.</li>}
            {records.notes.map((n) => (
              <li key={n.id}>
                <span className="muted">#{n.line_idx ?? '?'}</span> <strong>{n.term}</strong> ({n.note_type}) {n.note}{' '}
                <button type="button" className="link" onClick={() => act(deleteNote(dramaId, n.id))}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
          <h4>Translation memory suggestions</h4>
          <ul data-testid="tm-list">
            {records.tm.length === 0 && <li className="muted">No suggestions.</li>}
            {records.tm.map((s) => (
              <li key={s.entry_id}>
                <span className="muted">#{s.line_idx}</span> {s.suggestion}{' '}
                <span className="muted">({Math.round(s.similarity * 100)}%{s.exact ? ', exact' : ''})</span>{' '}
                {s.line_id !== null && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => act(acceptTm(dramaId, s.line_id as number, s.entry_id))}
                  >
                    Accept
                  </button>
                )}
              </li>
            ))}
          </ul>
          <h4>Translation versions</h4>
          <ul data-testid="versions-list">
            {records.versions.length === 0 && <li className="muted">No saved versions.</li>}
            {records.versions.map((v) => (
              <li key={v.id}>
                {v.label ?? `Version ${v.id}`} · {v.engine} {v.model}
                {v.is_active ? ' · active' : ''} <span className="muted">{v.created_at}</span>
              </li>
            ))}
          </ul>
          <h4>Line history</h4>
          <ul data-testid="history-list">
            {records.history.length === 0 && <li className="muted">No history snapshots.</li>}
            {records.history.map((h) => (
              <li key={h.id}>
                {h.label ?? `Snapshot ${h.id}`} <span className="muted">{h.created_at}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
