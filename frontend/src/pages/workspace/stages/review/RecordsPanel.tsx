import { useEffect, useState } from 'react'

import {
  acceptTm,
  deleteNote,
  listHistory,
  listNotes,
  listTmSuggestions,
  listVersions,
} from '../../../../api/review'
import { listAllLines, restoreSnapshot } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { TypedConfirm } from '../../../../components/TypedConfirm'
import type { HistoryItem, ReviewNote, TmSuggestion, VersionItem } from '../../../../types/review'
import { JOB_RUNNING_MESSAGE, structureErrorText } from './reviewLogic'

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
  jobRunning: boolean
}

// Notes (add lives on each line), translation versions (read-only), line
// history with a typed-confirm Restore, and translation-memory suggestions.
export function RecordsPanel({ dramaId, reloads, onChanged, jobRunning }: Props) {
  const [records, setRecords] = useState<Records | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [restoring, setRestoring] = useState<HistoryItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [restored, setRestored] = useState<string | null>(null)

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

  // The restore endpoint needs the drama's current line ids; the snapshot of
  // the current lines is taken by the server before anything is replaced.
  const restore = (h: HistoryItem) => {
    setBusy(true)
    listAllLines(dramaId)
      .then((all) => restoreSnapshot(dramaId, h.id, all.map((l) => l.id)))
      .then(
        () => {
          setError(null)
          setRestoring(null)
          setRestored(`Restored “${h.label ?? `Snapshot ${h.id}`}”. The lines before it are saved as a new snapshot.`)
          onChanged()
        },
        setError,
      )
      .finally(() => setBusy(false))
  }

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
      {structureErrorText(error) ? (
        <p className="error" role="alert">{structureErrorText(error)}</p>
      ) : (
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      )}
      {restored && <p role="status" data-testid="restore-status">{restored}</p>}
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
                {h.label ?? `Snapshot ${h.id}`} <span className="muted">{h.created_at}</span>{' '}
                {restoring?.id !== h.id && (
                  <button type="button" className="link" onClick={() => { setRestored(null); setRestoring(h) }}>
                    Restore…
                  </button>
                )}
                {restoring?.id === h.id && (
                  <TypedConfirm
                    word="restore"
                    action="Restore snapshot"
                    busy={busy}
                    blocked={jobRunning ? JOB_RUNNING_MESSAGE : null}
                    onConfirm={() => restore(h)}
                    onCancel={() => setRestoring(null)}
                  >
                    <p className="muted">
                      Replaces every line with “{h.label ?? `Snapshot ${h.id}`}” ({h.created_at}). Your current lines
                      are saved as a snapshot first.
                    </p>
                  </TypedConfirm>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}
