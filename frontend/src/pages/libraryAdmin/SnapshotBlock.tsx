/*
 * Library tools > Backup & storage > "Automatic backup snapshot" (roadmap
 * Step 43): the one snapshot automatic backups keep (date, size, kind, drama
 * count), restoring ONE drama from it, and deleting it. PC only (the parent
 * AdminSection shows the PC-only note away from the PC); nothing is fetched
 * until /api/meta has answered. The schedule itself lives in Settings.
 *
 * Restore: pick a drama from the snapshot's list (search when there are
 * many), read what will happen (a copy when the drama is still in the
 * library; no files from a database-only snapshot), then type RESTORE.
 */
import { useCallback, useEffect, useState } from 'react'

import { deleteSnapshot, getSnapshot, getSnapshotDramas, restoreSnapshotDrama } from '../../api/backups'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { TypedConfirm } from '../../components/TypedConfirm'
import { buttonClass } from '../../components/uiClasses'
import { mediaTypeLabel } from '../../labels'
import { routeHref } from '../../router'
import {
  DELETE_SNAPSHOT_WORD, RESTORE_SNAPSHOT_WORD, type RestoreDramaDone, type SnapshotDrama, type SnapshotDramaList,
  type SnapshotInfo,
} from '../../types/backups'
import { describeRestore, describeSnapshot, filterSnapshotDramas, restoreNotes } from '../backupsFormat'
import '../backups.css'

const SERVER = { pcOnly: true, serverText: true } as const
// Show the search box once the list is longer than this.
const SEARCH_FROM = 8

type Mode = 'idle' | 'restore' | 'delete'

export function SnapshotBlock() {
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [mode, setMode] = useState<Mode>('idle')
  const [notice, setNotice] = useState<string | null>(null)
  const [restored, setRestored] = useState<RestoreDramaDone | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    getSnapshot().then(setSnapshot, (e: unknown) => setLoadError(e))
  }, [])

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (live && getPcMode() !== 'remote') load()
    })
    return () => {
      live = false
    }
  }, [load])

  const usable = !!snapshot?.exists && snapshot.readable !== false
  const open = (next: Mode) => {
    setNotice(null)
    setRestored(null)
    setMode(next)
  }

  return (
    <div className="admin-block" data-testid="snapshot-block">
      <h3>Automatic backup snapshot</h3>
      <p data-testid="snapshot-info">{describeSnapshot(snapshot)}</p>
      <p className="muted">
        One snapshot is kept; each new backup replaces it. Schedule it in{' '}
        <a href={routeHref({ name: 'settings' })}>Settings</a>.
      </p>
      <ErrorBanner error={loadError} describe={SERVER} />
      {mode === 'idle' && (
        <div className="actions">
          <button type="button" disabled={!usable} onClick={() => open('restore')}>
            Restore one drama…
          </button>
          <button type="button" className="danger" disabled={!snapshot?.exists} onClick={() => open('delete')}>
            Delete snapshot…
          </button>
        </div>
      )}
      {mode === 'restore' && (
        <RestorePicker
          onDone={(r) => {
            setRestored(r)
            setMode('idle')
          }}
          onCancel={() => setMode('idle')}
        />
      )}
      {mode === 'delete' && (
        <DeleteSnapshot
          snapshot={snapshot}
          onDone={() => {
            setMode('idle')
            setNotice('Snapshot deleted.')
            load()
          }}
          onCancel={() => setMode('idle')}
        />
      )}
      {restored && (
        <div className="actions" role="status" data-testid="restore-result">
          <span>{describeRestore(restored)}</span>
          <ButtonLink href={routeHref({ name: 'drama', id: restored.drama_id, stage: null })} size="sm">
            Open drama
          </ButtonLink>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  )
}

function RestorePicker({ onDone, onCancel }: { onDone: (r: RestoreDramaDone) => void; onCancel: () => void }) {
  const [list, setList] = useState<SnapshotDramaList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<SnapshotDrama | null>(null)
  const [busy, setBusy] = useState(false)
  const [restoreError, setRestoreError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getSnapshotDramas().then(
      (l) => live && setList(l),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [])

  const restore = () => {
    if (!picked) return
    setBusy(true)
    setRestoreError(null)
    restoreSnapshotDrama(picked.id).then(
      (r) => {
        setBusy(false)
        onDone(r)
      },
      (e: unknown) => {
        setBusy(false)
        setRestoreError(e)
      },
    )
  }

  if (error) {
    return (
      <>
        <ErrorBanner error={error} describe={SERVER} />
        <div className="actions">
          <button type="button" className="link" onClick={onCancel}>Close</button>
        </div>
      </>
    )
  }
  if (!list) return <p className="muted">Loading the snapshot's dramas…</p>

  if (picked) {
    return (
      <div className="admin-block">
        <p>
          Restore <strong>{picked.title}</strong>{' '}
          <button type="button" className="link" disabled={busy} onClick={() => setPicked(null)}>
            Choose another
          </button>
        </p>
        <TypedConfirm
          word={RESTORE_SNAPSHOT_WORD}
          exact
          action="Restore drama"
          busy={busy}
          onConfirm={restore}
          onCancel={onCancel}
        >
          <ul className="restore-notes" data-testid="restore-notes">
            {restoreNotes(picked, list.kind).map((n) => <li key={n}>{n}</li>)}
          </ul>
        </TypedConfirm>
        {busy && <p className="muted" role="status">Restoring… keep this tab open.</p>}
        <ErrorBanner error={restoreError} describe={SERVER} />
      </div>
    )
  }

  const shown = filterSnapshotDramas(list.dramas, query)
  return (
    <div className="admin-block">
      {!list.dramas.length ? (
        <p className="muted">This snapshot has no dramas.</p>
      ) : (
        <>
          {list.dramas.length > SEARCH_FROM && (
            <Field label="Find a drama">
              <input type="search" value={query} placeholder="Title" onChange={(e) => setQuery(e.target.value)} />
            </Field>
          )}
          <p className="muted">Pick the drama to restore. Nothing changes until you confirm.</p>
          <ul className="snapshot-dramas" aria-label="Dramas in the snapshot">
            {shown.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => setPicked(d)}>
                  <span className="snapshot-drama-title">{d.title}</span>
                  <span className="snapshot-drama-meta">
                    {mediaTypeLabel(d.media_type)} · {d.line_count.toLocaleString('en-US')}{' '}
                    {d.line_count === 1 ? 'line' : 'lines'}
                    {d.exists_now ? ' · still in your library' : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {!shown.length && <p className="muted">No drama matches.</p>}
        </>
      )}
      <div className="actions">
        <button type="button" className={buttonClass('ghost')} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function DeleteSnapshot({ snapshot, onDone, onCancel }: {
  snapshot: SnapshotInfo | null
  onDone: () => void
  onCancel: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const run = () => {
    setBusy(true)
    setError(null)
    deleteSnapshot().then(
      () => {
        setBusy(false)
        onDone()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  return (
    <>
      <TypedConfirm word={DELETE_SNAPSHOT_WORD} exact autoFocus action="Delete snapshot" busy={busy} onConfirm={run} onCancel={onCancel}>
        <p>
          Deletes the snapshot ({describeSnapshot(snapshot)}). No undo. If automatic backups are on, the next run
          makes a new one.
        </p>
      </TypedConfirm>
      <ErrorBanner error={error} describe={SERVER} />
    </>
  )
}
