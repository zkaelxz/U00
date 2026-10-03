/*
 * The Trash list under Library tools > Disk usage: what "Move to Trash" put
 * aside. Restore puts an item back where it came from; "Delete permanently…"
 * and "Empty Trash…" are the only places anything is really deleted, and each
 * needs the word DELETE typed. Nothing here shows a file system path: only the
 * item's old place inside the data folder.
 */
import { useState } from 'react'

import { emptyTrash, purgeTrashItem, restoreTrashItem } from '../../api/diskUsage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { TypedConfirm } from '../../components/TypedConfirm'
import { buttonClass } from '../../components/uiClasses'
import type { DiskUsageTrashItem, DiskUsageTrashList } from '../../types/diskUsage'
import {
  TRASH_WORD, describeEmptied, formatBytes, describePurged, describeRestored, trashItemName, trashLine, trashSizeLine, trashedOn,
} from './diskUsageModel'

const SERVER = { pcOnly: true, serverText: true } as const

type Props = {
  trash: DiskUsageTrashList | null
  // A change happened (message to show); the caller reloads the trash and the scan.
  onChanged: (message: string) => void
  // A 409 or other failure that makes what is shown stale.
  onStale: () => void
}

export function TrashPanel({ trash, onChanged, onStale }: Props) {
  const [emptying, setEmptying] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  if (!trash) return null
  const blocked = trash.busy_reason
  const empty = () => {
    setBusy(true)
    setError(null)
    emptyTrash().then(
      (r) => { setBusy(false); setEmptying(false); onChanged(describeEmptied(r)) },
      (e: unknown) => { setBusy(false); setError(e); onStale() },
    )
  }
  return (
    <section aria-label="Trash" className="du-trash">
      <h3 className="du-trash-title">Trash</h3>
      <p className="du-trash-line" data-testid="trash-line">{trashLine(trash.size_bytes, trash.partial)}</p>
      {trash.items.length === 0 ? (
        <p className="muted">Trash is empty.</p>
      ) : (
        <>
          <p className="muted du-why">
            Items you moved to Trash stay inside Baihe&apos;s data folder until you restore or delete them here.
          </p>
          <ul className="du-list" aria-label="Items in Trash">
            {trash.items.map((t) => (
              <TrashRow key={t.id} item={t} blocked={blocked} onChanged={onChanged} onStale={onStale} />
            ))}
          </ul>
          <div className="du-actions">
            {!emptying && (
              <button
                type="button"
                className={buttonClass('secondary', 'sm')}
                disabled={!!blocked || busy}
                onClick={() => setEmptying(true)}
              >
                Empty Trash…
              </button>
            )}
          </div>
          {emptying && (
            <TypedConfirm
              word={TRASH_WORD}
              exact
              autoFocus
              action="Empty Trash permanently"
              busy={busy}
              blocked={blocked}
              onConfirm={empty}
              onCancel={() => setEmptying(false)}
            >
              <p className="du-warn">
                Permanently deletes all {trash.item_count} item{trash.item_count === 1 ? '' : 's'} in Trash and frees{' '}
                {formatBytes(trash.size_bytes)}. This can&apos;t be undone.
              </p>
            </TypedConfirm>
          )}
        </>
      )}
      {blocked && <p className="muted du-busy">{blocked}</p>}
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
    </section>
  )
}

function TrashRow({ item, blocked, onChanged, onStale }: {
  item: DiskUsageTrashItem
  blocked: string | null
  onChanged: (message: string) => void
  onStale: () => void
}) {
  const [busy, setBusy] = useState<'restore' | 'purge' | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const name = trashItemName(item)
  const fail = (e: unknown) => {
    setBusy(null)
    setError(e)
    // A 409 means it changed or a job started: show the new state.
    if ((e as { status?: number } | null)?.status === 409) onStale()
  }
  const restore = () => {
    setBusy('restore')
    setError(null)
    restoreTrashItem(item.id).then(
      () => { setBusy(null); onChanged(describeRestored(name)) },
      fail,
    )
  }
  const purge = () => {
    setBusy('purge')
    setError(null)
    purgeTrashItem(item).then(
      (r) => { setBusy(null); setDeleting(false); onChanged(describePurged(name, r.freed_bytes)) },
      fail,
    )
  }
  return (
    <li className="du-row du-trash-row">
      <div className="du-row-main">
        <span className="du-name">{name}</span>
        <span className="du-size num">{trashSizeLine(item)}</span>
      </div>
      {trashedOn(item) && <p className="muted du-why">{`Moved to Trash on ${trashedOn(item)}`}</p>}
      {!item.restorable && (
        <p className="muted du-why">
          Restore may not work right now: its old folder is gone, its name is taken, or its record is damaged.
          You can still delete it.
        </p>
      )}
      <div className="du-actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          aria-label={`Restore ${name}`}
          disabled={!!blocked || busy !== null}
          onClick={restore}
        >
          {busy === 'restore' ? 'Working…' : 'Restore'}
        </button>
        {!deleting && (
          <button
            type="button"
            className={buttonClass('secondary', 'sm')}
            aria-label={`Delete ${name} permanently`}
            disabled={!!blocked || busy !== null}
            onClick={() => setDeleting(true)}
          >
            Delete permanently…
          </button>
        )}
      </div>
      {deleting && (
        <TypedConfirm
          word={TRASH_WORD}
          exact
          autoFocus
          action={`Delete ${name} permanently`}
          busy={busy === 'purge'}
          blocked={blocked}
          onConfirm={purge}
          onCancel={() => setDeleting(false)}
        >
          <p className="du-warn">
            Permanently deletes {name} ({trashSizeLine(item)}) from this PC. This frees the space and can&apos;t be undone.
          </p>
        </TypedConfirm>
      )}
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
    </li>
  )
}
