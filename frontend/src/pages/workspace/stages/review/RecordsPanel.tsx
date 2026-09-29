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
import { Section } from '../../../../components/Section'
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

  if (!records) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  const { notes, tm, versions, history } = records
  const counts: [string, number][] = [
    ['Notes', notes.length],
    ['TM suggestions', tm.length],
    ['Versions', versions.length],
    ['History', history.length],
  ]
  const total = counts.reduce((n, [, c]) => n + c, 0)
  // No empty panel: nothing to show means nothing is rendered.
  if (total === 0) return <ErrorBanner error={error} onDismiss={() => setError(null)} />

  return (
    <Section
      storageKey="review.records"
      title="Records"
      count={total}
      summary={counts.filter(([, c]) => c > 0).map(([l, c]) => `${l} ${c}`).join(' · ')}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {notes.length > 0 && (
        <>
          <h4>Notes</h4>
          <ul data-testid="notes-list">
            {notes.map((n) => (
              <li key={n.id}>
                <span className="muted">#{n.line_idx ?? '?'}</span> <strong>{n.term}</strong> ({n.note_type}) {n.note}{' '}
                <button type="button" className="link" onClick={() => act(deleteNote(dramaId, n.id))}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {tm.length > 0 && (
        <>
          <h4>Translation memory suggestions</h4>
          <ul data-testid="tm-list">
            {tm.map((s) => (
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
        </>
      )}
      {versions.length > 0 && (
        <>
          <h4>Translation versions</h4>
          <ul data-testid="versions-list">
            {versions.map((v) => (
              <li key={v.id}>
                {v.label ?? `Version ${v.id}`} · {v.engine} {v.model}
                {v.is_active ? ' · active' : ''} <span className="muted">{v.created_at}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {history.length > 0 && (
        <>
          <h4>Line history</h4>
          <ul data-testid="history-list">
            {history.map((h) => (
              <li key={h.id}>
                {h.label ?? `Snapshot ${h.id}`} <span className="muted">{h.created_at}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}
