import { useEffect, useRef, useState } from 'react'

import {
  acceptTm,
  activateVersion,
  deleteNote,
  listHistory,
  listNotes,
  listTmSuggestions,
  listVersions,
} from '../../../../api/review'
import { listAllLines, restoreSnapshot } from '../../../../api/restructure'
import { deleteVersion } from '../../../../api/stageDeletes'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { TypedConfirm } from '../../../../components/TypedConfirm'
import type { HistoryItem, ReviewNote, TmSuggestion, VersionItem } from '../../../../types/review'
import { JOB_RUNNING_MESSAGE, keptNote, structureErrorText } from './reviewLogic'
import type { GoToLine } from './reviewResults'
import { dismissTmEverywhere, useTmDismissed, visibleTm } from './tmDismiss'
import { lineNumber } from '../../../../lineNumber'
import { capFirst } from '../../../../labels'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { buttonClass } from '../../../../components/uiClasses'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../../../../hooks/usePcOnly'

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
  // Opens a line in the editor; resolves to null, or a message saying why not.
  onGoTo: GoToLine
}

// Notes (add lives on each line), translation versions (use or delete), line
// history with a typed-confirm Restore, and translation-memory suggestions.
export function RecordsPanel({ dramaId, reloads, onChanged, jobRunning, onGoTo }: Props) {
  const [records, setRecords] = useState<Records | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [restoring, setRestoring] = useState<HistoryItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [restored, setRestored] = useState<string | null>(null)
  const pc = usePcOnly()
  const [versionError, setVersionError] = useState<unknown>(null)
  const [activating, setActivating] = useState<number | null>(null)
  const [activated, setActivated] = useState<string | null>(null)
  const [activateError, setActivateError] = useState<unknown>(null)
  const [jumpNote, setJumpNote] = useState<string | null>(null)
  const tmDismissed = useTmDismissed(dramaId)

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

  // After a version delete, focus moves to the next row's Delete button (or
  // the previous one), else to the Records summary, once the list reloads.
  const versionsRef = useRef<HTMLUListElement>(null)
  const focusAfterDelete = useRef<{ deletedId: number; nextId: number | null; summary: HTMLElement | null } | null>(null)
  useEffect(() => {
    const f = focusAfterDelete.current
    if (!f || !records || records.versions.some((v) => v.id === f.deletedId)) return
    focusAfterDelete.current = null
    const next = f.nextId === null ? null
      : versionsRef.current?.querySelector<HTMLButtonElement>(`li[data-version-id="${f.nextId}"] button`)
    const target = next ?? (f.summary?.isConnected ? f.summary : null)
    target?.focus()
  }, [records])

  const removeVersion = (v: VersionItem) => {
    setVersionError(null)
    const list = records?.versions ?? []
    const i = list.findIndex((x) => x.id === v.id)
    const next = list[i + 1] ?? list[i - 1] ?? null
    const summary = versionsRef.current?.closest('details')?.querySelector<HTMLElement>(':scope > summary') ?? null
    deleteVersion(dramaId, v.id).then(
      () => {
        setError(null)
        focusAfterDelete.current = { deletedId: v.id, nextId: next?.id ?? null, summary }
        onChanged()
      },
      setVersionError,
    )
  }

  // "Use this version": the server rewrites only each line's English (by
  // line id) after saving a snapshot, then the panel and lines reload.
  const switchToVersion = (v: VersionItem) => {
    setActivateError(null)
    setActivated(null)
    setActivating(v.id)
    activateVersion(dramaId, v.id)
      .then(
        (r) => {
          setError(null)
          setActivated(
            `Now using “${r.label || `Version ${v.id}`}” (${r.lines_changed} line${r.lines_changed === 1 ? '' : 's'} changed). The lines before it are saved in Line history.` +
              keptNote(r.conflicts?.length ?? 0),
          )
          onChanged()
        },
        setActivateError,
      )
      .finally(() => setActivating(null))
  }

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

  // A note's line (R43): the editor above opens it, on whatever page it is.
  const jumpTo = (lineId: number) => {
    setJumpNote(null)
    void onGoTo({ lineId }).then(setJumpNote)
  }

  if (!records) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  const { notes, versions, history } = records
  const tm = visibleTm(records.tm, tmDismissed)
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
                <span className="muted">#{n.line_idx === null ? '?' : lineNumber(n.line_idx)}</span> <strong>{n.term}</strong> ({n.note_type}) {n.note}{' '}
                {n.line_id !== null && (
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => jumpTo(n.line_id as number)}>
                    Go to line
                  </button>
                )}{' '}
                <button type="button" className={buttonClass('danger', 'sm')} onClick={() => act(deleteNote(dramaId, n.id))}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
          {jumpNote && <p role="status" data-testid="note-jump-status">{jumpNote}</p>}
        </>
      )}
      {tm.length > 0 && (
        <>
          <h4>Translation memory suggestions</h4>
          <ul data-testid="tm-list">
            {tm.map((s) => (
              <li key={s.entry_id}>
                <span className="muted">#{lineNumber(s.line_idx)}</span> {s.suggestion}{' '}
                <span className="muted">({Math.round(s.similarity * 100)}%{s.exact ? ', exact' : ''})</span>{' '}
                {s.line_id !== null && (
                  <button
                    type="button"
                    className={buttonClass('secondary', 'sm')}
                    onClick={() => act(acceptTm(dramaId, s.line_id as number, s.entry_id, s.en))}
                  >
                    Accept
                  </button>
                )}{' '}
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => dismissTmEverywhere(dramaId, s)}>
                  Dismiss
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {versions.length > 0 && (
        <>
          <h4>Translation versions</h4>
          <ul data-testid="versions-list" ref={versionsRef}>
            {versions.map((v) => (
              <li key={v.id} data-version-id={v.id}>
                {v.label ?? `Version ${v.id}`} · {v.engine} {v.model}
                {v.is_active ? ' · active' : ''} <span className="muted">{v.created_at}</span>{' '}
                {pc === 'local' && (
                  <ConfirmButton
                    name={v.label ?? `Version ${v.id}`}
                    disabled={jobRunning}
                    onConfirm={() => removeVersion(v)}
                  />
                )}
                {!v.is_active && (
                  <ConfirmButton
                    name={v.label ?? `Version ${v.id}`}
                    label="Use this version…"
                    verb="use"
                    tone="primary"
                    confirmLabel={`Confirm: replace the English with ${v.label ?? `Version ${v.id}`}`}
                    busy={activating === v.id}
                    disabled={jobRunning || (activating !== null && activating !== v.id)}
                    onConfirm={() => switchToVersion(v)}
                  />
                )}
              </li>
            ))}
          </ul>
          {activated && <p role="status" data-testid="activate-status">{activated}</p>}
          <ErrorBanner error={activateError} onDismiss={() => setActivateError(null)} />
          {jobRunning && <p className="muted">Wait for the running job to finish.</p>}
          {pc === 'remote' && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
          <ErrorBanner error={versionError} describe={{ pcOnly: true }} onDismiss={() => setVersionError(null)} />
        </>
      )}
      {history.length > 0 && (
        <>
          <h4>Line history</h4>
          <ul data-testid="history-list">
            {history.map((h) => (
              <li key={h.id}>
                {capFirst(h.label ?? `Snapshot ${h.id}`)} <span className="muted">{h.created_at}</span>{' '}
                {restoring?.id !== h.id && (
                  <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { setRestored(null); setRestoring(h) }}>
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
