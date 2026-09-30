/*
 * Library tools > Backup & storage > "Automatic backup copies" (roadmap
 * Step 43): the rotating copies automatic backups keep (the newest one's
 * date, size, kind and drama count), restoring ONE drama from a chosen copy,
 * and deleting one copy or all of them. PC only (the parent AdminSection
 * shows the PC-only note away from the PC); nothing is fetched until
 * /api/meta has answered. The schedule itself lives in Settings.
 *
 * Restore: choose the copy ("Restore from", the newest by default), pick a
 * drama from its list (search when there are many), read what will happen
 * (a copy when the drama is still in the library; no files from a
 * database-only copy), then type RESTORE.
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
  DELETE_SNAPSHOT_WORD, RESTORE_SNAPSHOT_WORD, type RestoreDramaDone, type SnapshotCopy, type SnapshotDrama,
  type SnapshotDramaList, type SnapshotInfo,
} from '../../types/backups'
import {
  ROTATION_NOTE, describeCopy, describeRestore, describeSnapshot, filterSnapshotDramas, formatWhen, restoreNotes,
} from '../backupsFormat'
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
  const copies = snapshot?.copies ?? []
  const open = (next: Mode) => {
    setNotice(null)
    setRestored(null)
    setMode(next)
  }

  return (
    <div className="admin-block" data-testid="snapshot-block">
      <h3>Automatic backup copies</h3>
      <p data-testid="snapshot-info">{describeSnapshot(snapshot)}</p>
      <p className="muted">
        {ROTATION_NOTE} Schedule them in <a href={routeHref({ name: 'settings' })}>Settings</a>.
      </p>
      <ErrorBanner error={loadError} describe={SERVER} />
      {mode === 'idle' && (
        <div className="actions">
          <button type="button" disabled={!usable} onClick={() => open('restore')}>
            Restore one drama…
          </button>
          <button type="button" className="danger" disabled={!snapshot?.exists} onClick={() => open('delete')}>
            Delete a copy…
          </button>
        </div>
      )}
      {mode === 'restore' && (
        <RestorePicker
          copies={copies.filter((c) => c.readable)}
          onDone={(r) => {
            setRestored(r)
            setMode('idle')
          }}
          onCancel={() => setMode('idle')}
        />
      )}
      {mode === 'delete' && (
        <DeleteSnapshot
          copies={copies}
          onDone={(all) => {
            setMode('idle')
            setNotice(all ? 'All copies deleted.' : 'Copy deleted.')
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

function RestorePicker({ copies, onDone, onCancel }: {
  // The readable copies, newest first.
  copies: SnapshotCopy[]
  onDone: (r: RestoreDramaDone) => void
  onCancel: () => void
}) {
  // "" = the newest readable copy (the server's default).
  const [from, setFrom] = useState(copies[0]?.name ?? '')
  const [list, setList] = useState<SnapshotDramaList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<SnapshotDrama | null>(null)
  const [busy, setBusy] = useState(false)
  const [restoreError, setRestoreError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getSnapshotDramas(from || undefined).then(
      (l) => live && setList(l),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [from])

  const restore = () => {
    if (!picked || !list) return
    setBusy(true)
    setRestoreError(null)
    // The copy the list came from, so the restore reads the same one.
    restoreSnapshotDrama(picked.id, list.name).then(
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

  const chooser = copies.length > 0 && (
    <Field label="Restore from">
      <select
        value={from}
        disabled={busy}
        onChange={(e) => {
          setFrom(e.target.value)
          setList(null)
          setError(null)
          setPicked(null)
          setQuery('')
        }}
      >
        {copies.map((c) => (
          <option key={c.name} value={c.name}>
            {describeCopy(c)}
          </option>
        ))}
      </select>
    </Field>
  )
  if (error) {
    return (
      <>
        {chooser}
        <ErrorBanner error={error} describe={SERVER} />
        <div className="actions">
          <button type="button" className="link" onClick={onCancel}>Close</button>
        </div>
      </>
    )
  }
  if (!list) {
    return (
      <div className="admin-block">
        {chooser}
        <p className="muted">Loading the copy's dramas…</p>
      </div>
    )
  }

  const fromWhen = formatWhen(copies.find((c) => c.name === list.name)?.created_at)
  if (picked) {
    return (
      <div className="admin-block">
        <p>
          Restore <strong>{picked.title}</strong>
          {fromWhen ? ` from the copy of ${fromWhen}` : ''}{' '}
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
      {chooser}
      {!list.dramas.length ? (
        <p className="muted">This copy has no dramas.</p>
      ) : (
        <>
          {list.dramas.length > SEARCH_FROM && (
            <Field label="Find a drama">
              <input type="search" value={query} placeholder="Title" onChange={(e) => setQuery(e.target.value)} />
            </Field>
          )}
          <p className="muted">Pick the drama to restore. Nothing changes until you confirm.</p>
          <ul className="snapshot-dramas" aria-label="Dramas in the copy">
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

// The delete choice for every copy at once.
const ALL = ''

function DeleteSnapshot({ copies, onDone, onCancel }: {
  // Every copy, newest first.
  copies: SnapshotCopy[]
  onDone: (all: boolean) => void
  onCancel: () => void
}) {
  // The oldest copy by default: the one least likely to be missed.
  const [which, setWhich] = useState(copies.length ? copies[copies.length - 1].name : ALL)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const chosen = copies.find((c) => c.name === which)
  const run = () => {
    setBusy(true)
    setError(null)
    const all = which === ALL
    deleteSnapshot(all ? { all: true } : { snapshot: which }).then(
      () => {
        setBusy(false)
        onDone(all)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  return (
    <>
      <Field label="Copy to delete">
        <select value={which} disabled={busy} onChange={(e) => setWhich(e.target.value)}>
          {copies.map((c) => (
            <option key={c.name} value={c.name}>
              {describeCopy(c)}
            </option>
          ))}
          <option value={ALL}>All copies ({copies.length})</option>
        </select>
      </Field>
      <TypedConfirm
        word={DELETE_SNAPSHOT_WORD}
        exact
        action={chosen ? 'Delete copy' : 'Delete all copies'}
        busy={busy}
        onConfirm={run}
        onCancel={onCancel}
      >
        <p>
          {chosen
            ? `Deletes the copy from ${describeCopy(chosen)}. No undo.`
            : `Deletes all ${copies.length} copies. No undo.`}{' '}
          If automatic backups are on, the next run makes a new one.
        </p>
      </TypedConfirm>
      <ErrorBanner error={error} describe={SERVER} />
    </>
  )
}
