/*
 * Library tools > Backup & storage > "Automatic backup copies" (roadmap
 * Step 43): the rotating copies automatic backups keep (the newest one's
 * date, size, kind and drama count), restoring ONE drama from a chosen copy,
 * and deleting one copy or all of them. PC only (the parent AdminSection
 * shows the PC-only note away from the PC); nothing is fetched until
 * /api/meta has answered. The schedule itself lives in Settings.
 *
 * Restore: choose the copy ("Restore from": the server's default copy, or
 * none when the newest can't be told for sure -- then the owner must pick),
 * pick a drama from its list (search when there are many), read what will
 * happen (a copy when the drama is still in the library; no files from a
 * database-only copy; a warning for a copy this library doesn't manage),
 * then type RESTORE. Copies this library doesn't manage are listed apart
 * and are deleted only when chosen by name or with "including not managed".
 * "From a backup file…" imports chosen dramas from an uploaded backup file
 * instead (BackupFileImport).
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
  DELETE_SNAPSHOT_WORD, RESTORE_SNAPSHOT_WORD, type ImportDramasDone, type RestoreDramaDone, type SnapshotCopy, type SnapshotDrama,
  type SnapshotDramaList, type SnapshotInfo,
} from '../../types/backups'
import {
  CHOOSE_COPY_TEXT, ROTATION_NOTE, UNMANAGED_DELETE_WARNING, UNMANAGED_LABEL, UNMANAGED_RESTORE_WARNING,
  chooseCopyCandidates, describeCopy, describeRestore, describeSnapshot, filterSnapshotDramas, formatWhen,
  initialRestoreCopy, isManaged, restoreNotes, splitCopies,
} from '../backupsFormat'
import { describeImport } from '../backupFileImportModel'
import '../backups.css'
import { BackupFileImport } from './BackupFileImport'

const SERVER = { pcOnly: true, serverText: true } as const
// Show the search box once the list is longer than this.
const SEARCH_FROM = 8

type Mode = 'idle' | 'restore' | 'import' | 'delete'

export function SnapshotBlock() {
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [mode, setMode] = useState<Mode>('idle')
  const [notice, setNotice] = useState<string | null>(null)
  const [restored, setRestored] = useState<RestoreDramaDone | null>(null)
  const [imported, setImported] = useState<ImportDramasDone | null>(null)

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
    setImported(null)
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
          <button type="button" onClick={() => open('import')}>
            From a backup file…
          </button>
          <button type="button" className="danger" disabled={!snapshot?.exists} onClick={() => open('delete')}>
            Delete a copy…
          </button>
        </div>
      )}
      {mode === 'restore' && (
        <RestorePicker
          copies={copies.filter((c) => c.readable)}
          initial={initialRestoreCopy(snapshot, copies.filter((c) => c.readable))}
          onDone={(r) => {
            setRestored(r)
            setMode('idle')
          }}
          onCancel={() => setMode('idle')}
        />
      )}
      {mode === 'import' && (
        <BackupFileImport
          onDone={(r) => {
            setImported(r)
            setMode('idle')
          }}
          onCancel={() => setMode('idle')}
        />
      )}
      {mode === 'delete' && (
        <DeleteSnapshot
          copies={copies}
          onDone={(all, kept) => {
            setMode('idle')
            setNotice(
              !all
                ? 'Copy deleted.'
                : kept
                  ? `This library's copies deleted; ${kept} other or older ${kept === 1 ? 'copy was' : 'copies were'} left.`
                  : 'All copies deleted.',
            )
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
      {imported && (
        <div className="actions" role="status" data-testid="import-result">
          <span>{describeImport(imported)}</span>
          {imported.imported.length === 1 && (
            <ButtonLink href={routeHref({ name: 'drama', id: imported.imported[0].drama_id, stage: null })} size="sm">
              Open drama
            </ButtonLink>
          )}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  )
}

function RestorePicker({ copies, initial, onDone, onCancel }: {
  // The readable copies, newest first.
  copies: SnapshotCopy[]
  // The server's default copy, or "" when the owner must choose one.
  initial: string
  onDone: (r: RestoreDramaDone) => void
  onCancel: () => void
}) {
  // "" = nothing chosen yet: nothing is loaded until the owner picks a copy.
  const [from, setFrom] = useState(initial)
  const [list, setList] = useState<SnapshotDramaList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<SnapshotDrama | null>(null)
  const [busy, setBusy] = useState(false)
  const [restoreError, setRestoreError] = useState<unknown>(null)

  useEffect(() => {
    if (!from) return
    let live = true
    getSnapshotDramas(from).then(
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

  const { managed, unmanaged } = splitCopies(copies)
  const chosen = copies.find((c) => c.name === from)
  const chooser = copies.length > 0 && (
    <>
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
          {!from && (
            <option value="" disabled>
              Choose a copy…
            </option>
          )}
          {managed.map((c) => (
            <option key={c.name} value={c.name}>
              {describeCopy(c)}
            </option>
          ))}
          {unmanaged.length > 0 && (
            <optgroup label={UNMANAGED_LABEL}>
              {unmanaged.map((c) => (
                <option key={c.name} value={c.name}>
                  {describeCopy(c)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </Field>
      {chosen && !isManaged(chosen) && (
        <p className="error" data-testid="unmanaged-restore-warning">{UNMANAGED_RESTORE_WARNING}</p>
      )}
    </>
  )
  if (!from || chooseCopyCandidates(error)) {
    return (
      <div className="admin-block">
        {chooser}
        <p className="muted" role="status" data-testid="choose-copy">{CHOOSE_COPY_TEXT}</p>
        <div className="actions">
          <button type="button" className={buttonClass('ghost')} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    )
  }
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

// The delete choices for every copy at once: this library's, or every copy
// including the ones it doesn't manage.
const ALL = ''
const ALL_INCLUDING = '*all-including-unmanaged'

function DeleteSnapshot({ copies, onDone, onCancel }: {
  // Every copy, newest first.
  copies: SnapshotCopy[]
  onDone: (all: boolean, keptUnmanaged: number) => void
  onCancel: () => void
}) {
  const { managed, unmanaged } = splitCopies(copies)
  // The oldest managed copy by default: the one least likely to be missed.
  const [which, setWhich] = useState(
    managed.length ? managed[managed.length - 1].name : unmanaged.length ? unmanaged[unmanaged.length - 1].name : ALL,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const chosen = copies.find((c) => c.name === which)
  const risky = which === ALL_INCLUDING || (!!chosen && !isManaged(chosen))
  const run = () => {
    setBusy(true)
    setError(null)
    const all = !chosen
    const extra = risky ? { include_unmanaged: true as const } : {}
    deleteSnapshot(all ? { all: true, ...extra } : { snapshot: which, ...extra }).then(
      (r) => {
        setBusy(false)
        onDone(all, r.kept_unmanaged ?? 0)
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
          {managed.map((c) => (
            <option key={c.name} value={c.name}>
              {describeCopy(c)}
            </option>
          ))}
          {unmanaged.length > 0 && (
            <optgroup label={UNMANAGED_LABEL}>
              {unmanaged.map((c) => (
                <option key={c.name} value={c.name}>
                  {describeCopy(c)}
                </option>
              ))}
            </optgroup>
          )}
          {managed.length > 0 && (
            <option value={ALL}>
              {unmanaged.length ? `All this library's copies (${managed.length})` : `All copies (${managed.length})`}
            </option>
          )}
          {unmanaged.length > 0 && (
            <option value={ALL_INCLUDING}>
              All copies, including {unmanaged.length} not managed ({copies.length})
            </option>
          )}
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
        {risky && (
          <p className="error" data-testid="unmanaged-delete-warning">
            {chosen ? UNMANAGED_DELETE_WARNING : `${unmanaged.length} of these are not managed by this library. ${UNMANAGED_DELETE_WARNING}`}
          </p>
        )}
        <p>
          {chosen
            ? `Deletes the copy from ${describeCopy(chosen)}. No undo.`
            : which === ALL_INCLUDING
              ? `Deletes all ${copies.length} copies. No undo.`
              : `Deletes this library's ${managed.length} ${managed.length === 1 ? 'copy' : 'copies'}. No undo.`}{' '}
          If automatic backups are on, the next run makes a new one.
        </p>
      </TypedConfirm>
      <ErrorBanner error={error} describe={SERVER} />
    </>
  )
}
