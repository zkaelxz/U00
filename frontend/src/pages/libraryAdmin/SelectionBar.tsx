import { useEffect, useRef, useState, type ReactNode } from 'react'

import { ApiError } from '../../api/client'
import { bulkDelete, bulkSetStatus, bulkSetTag, bulkTranslate, startExport } from '../../api/libraryAdmin'
import type { DramaSummary } from '../../api/types'
import { ErrorBanner } from '../../components/ErrorBanner'
import { TypedConfirm } from '../../components/TypedConfirm'
import type { PcMode } from '../../hooks/usePcOnly'
import { statusLabel, tagLabel } from '../../labels'
import {
  ADMIN_JOB_IDS, LIBRARY_LIST_TAGS, LIBRARY_STATUSES, type LibraryListTag, type LibraryStatus,
} from '../../types/libraryAdmin'
import { AdminJobLine } from './AdminJobLine'
import {
  EXPORT_NEEDS, TRANSLATE_NEEDS, describeBulkResult, describeDeleteResult, describeTranslateSkips,
  exportableIds, titleOf, translatableIds,
} from './libraryAdmin'
import { useAdminJob, type AdminJob } from './useAdminJob'

type Props = {
  selected: DramaSummary[]
  // Every loaded drama (names for dramas already gone from the selection).
  items: DramaSummary[]
  pc: PcMode
  phone: boolean
  // The page-level export job (shared with Backup & storage).
  exporter: AdminJob
  onClear: () => void
  // Phone: leave select mode.
  onDone: () => void
  // Something changed server-side: reload the list.
  onChanged: () => void
  onDeleted: (ids: number[]) => void
  // The last bulk result line; the page keeps it after the bar unmounts.
  onResult: (text: string | null) => void
  // Phone: the page's result line, shown inside the bar while it is open.
  result?: string | null
}

const translateError = (e: unknown): string | null => {
  if (!(e instanceof ApiError)) return null
  if (e.status === 409) return 'A bulk translation is already running.'
  if (e.status === 403) return "Bulk translate uses paid engines, which this account can't use."
  return null
}

const PC_ONLY_NOTE = 'Delete and export are PC only.'
// PC-only calls (delete, export): a 403 means "not at the main PC".
const PC_ONLY_ERR = { pcOnly: true, serverText: true } as const

/** The sticky bar shown while at least one drama is selected (desktop), or in select mode (phone). */
export function SelectionBar({
  selected, items, pc, phone, exporter, onClear, onDone, onChanged, onDeleted, onResult, result,
}: Props) {
  const [status, setStatus] = useState<LibraryStatus>('translated')
  const [tag, setTag] = useState<LibraryListTag>('Favorite')
  const [busy, setBusy] = useState(false)
  // admin.library calls (status, lists): default error copy.
  const [adminError, setAdminError] = useState<unknown>(null)
  // local_only calls (delete): PC-only copy.
  const [deleteError, setDeleteError] = useState<unknown>(null)
  const [deleting, setDeleting] = useState(false)
  const [skips, setSkips] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDetailsElement>(null)
  const summaryRef = useRef<HTMLElement>(null)
  const translate = useAdminJob(ADMIN_JOB_IDS.translate)

  const ids = selected.map((d) => d.id)
  const toTranslate = translatableIds(selected)
  const toExport = exportableIds(selected)
  const local = pc !== 'remote'
  const name = (id: number) => {
    const d = items.find((x) => x.id === id)
    return d ? titleOf(d) : `#${id}`
  }

  const closeMenu = (refocus: boolean) => {
    // Close the element itself too: its toggle event (which sets menuOpen)
    // may not have run yet, and then setMenuOpen(false) changes nothing.
    if (menuRef.current) menuRef.current.open = false
    setMenuOpen(false)
    if (refocus) summaryRef.current?.focus()
  }

  // Phone menu: an outside tap closes it and returns focus to Actions.
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) {
        setMenuOpen(false)
        summaryRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [menuOpen])

  const run = <T,>(call: () => Promise<T>, done: (r: T) => string) => {
    closeMenu(false)
    setBusy(true)
    setAdminError(null)
    onResult(null)
    call().then(
      (r) => {
        setBusy(false)
        onResult(done(r))
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setAdminError(e)
      },
    )
  }

  const startTranslate = () => {
    setSkips(null)
    void translate.start(() => bulkTranslate(toTranslate)).then((r) => {
      if (r) setSkips(describeTranslateSkips(r.skipped, name))
    })
  }

  const remove = () => {
    setBusy(true)
    setDeleteError(null)
    bulkDelete(ids).then(
      (r) => {
        setBusy(false)
        setDeleting(false)
        onResult(describeDeleteResult(r, name))
        onDeleted(r.results.filter((x) => x.ok).map((x) => x.drama_id))
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setDeleteError(e)
      },
    )
  }

  const n = selected.length
  const translateMsg = translateError(translate.startError)
  // Reasons sit under their buttons in the phone menu, in the bar's lines on desktop.
  const reason = (show: boolean, id: string, text: string): ReactNode =>
    show ? <p className="muted" id={id}>{text}</p> : null
  const translateReason = reason(!toTranslate.length && n > 0, 'translate-needs', TRANSLATE_NEEDS)
  const exportReason = reason(local && !toExport.length && n > 0, 'export-needs', EXPORT_NEEDS)
  const pcReason = reason(!local, 'pc-only-note', PC_ONLY_NOTE)

  const actions = (
    <div className="bar-actions">
      <div className="bar-group">
        <select aria-label="New status" value={status} onChange={(e) => setStatus(e.target.value as LibraryStatus)}>
          {LIBRARY_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetStatus(ids, status), describeBulkResult)}>
          Set status
        </button>
      </div>
      <div className="bar-group">
        <select aria-label="List" value={tag} onChange={(e) => setTag(e.target.value as LibraryListTag)}>
          {LIBRARY_LIST_TAGS.map((t) => <option key={t} value={t}>{tagLabel(t)}</option>)}
        </select>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetTag(ids, tag, true), describeBulkResult)}>
          Add to list
        </button>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetTag(ids, tag, false), describeBulkResult)}>
          Remove from list
        </button>
      </div>
      <div className="bar-group">
        <button
          type="button"
          disabled={!toTranslate.length || translate.active}
          aria-describedby={!toTranslate.length ? 'translate-needs' : undefined}
          onClick={startTranslate}
        >
          Translate {toTranslate.length}
        </button>
        {phone && translateReason}
        {local && (
          <button
            type="button"
            disabled={!toExport.length || exporter.active}
            aria-describedby={!toExport.length ? 'export-needs' : undefined}
            onClick={() => void exporter.start(() => startExport(toExport))}
          >
            Export .zip ({toExport.length})
          </button>
        )}
        {phone && exportReason}
        {local && (
          <button
            type="button"
            className="danger"
            disabled={busy || !n}
            onClick={() => {
              closeMenu(false)
              setDeleting(true)
            }}
          >
            Delete…
          </button>
        )}
        {phone && pcReason}
      </div>
    </div>
  )

  const statusLines = (
    <>
      {!phone && translateReason}
      {!phone && exportReason}
      {!phone && pcReason}
      <AdminJobLine job={translate} busyText="Translating…" />
      {translateMsg ? <p className="error" role="alert">{translateMsg}</p> : <ErrorBanner error={translate.startError} />}
      {skips && <p className="muted">{skips}</p>}
      {local && <AdminJobLine job={exporter} busyText="Exporting…" artifact="export" showLink={exporter.done} />}
      {local && <ErrorBanner error={exporter.startError} describe={PC_ONLY_ERR} />}
      {deleting && local && (
        <TypedConfirm
          word="DELETE"
          exact
          autoFocus
          action={`Delete ${n}`}
          busy={busy}
          onConfirm={remove}
          onCancel={() => setDeleting(false)}
        >
          <p>Permanently deletes {n} drama{n === 1 ? '' : 's'} with their lines and files. No undo.</p>
        </TypedConfirm>
      )}
      {result && <p role="status" data-testid="bulk-result">{result}</p>}
      <ErrorBanner error={adminError} />
      <ErrorBanner error={deleteError} describe={PC_ONLY_ERR} />
    </>
  )

  if (phone) {
    return (
      <section className="selection-bar phone" aria-label="Selection">
        <div className="bar-row">
          <strong data-testid="selected-count">{n} selected</strong>
          <details
            ref={menuRef}
            className="bar-menu"
            open={menuOpen}
            onToggle={(e) => setMenuOpen(e.currentTarget.open)}
            onKeyDown={(e) => {
              if (e.currentTarget.open && e.key === 'Escape') {
                e.stopPropagation()
                closeMenu(true)
              }
            }}
          >
            <summary ref={summaryRef}>Actions</summary>
            <div className="bar-menu-body">
              {actions}
              <button type="button" className="link" onClick={() => { closeMenu(true); onClear() }}>Clear</button>
            </div>
          </details>
          <button type="button" onClick={onDone}>Done</button>
        </div>
        {statusLines}
      </section>
    )
  }

  return (
    <section className="selection-bar wide" aria-label="Selection">
      <div className="bar-row">
        <strong data-testid="selected-count">{n} selected</strong>
        {actions}
        <button type="button" className="link" onClick={onClear}>Clear</button>
      </div>
      {statusLines}
    </section>
  )
}
