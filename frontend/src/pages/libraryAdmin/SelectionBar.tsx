import { useState } from 'react'

import { ApiError } from '../../api/client'
import { bulkDelete, bulkSetStatus, bulkSetTag, bulkTranslate, startExport } from '../../api/libraryAdmin'
import type { DramaSummary } from '../../api/types'
import { ErrorBanner } from '../../components/ErrorBanner'
import { TypedConfirm } from '../../components/TypedConfirm'
import type { PcMode } from '../../hooks/usePcOnly'
import {
  ADMIN_JOB_IDS, LIBRARY_LIST_TAGS, LIBRARY_STATUSES, type LibraryListTag, type LibraryStatus,
} from '../../types/libraryAdmin'
import { AdminJobLine } from './AdminJobLine'
import {
  TRANSLATE_NEEDS, describeBulkResult, describeDeleteResult, describeTranslateSkips, exportableIds,
  titleOf, translatableIds,
} from './libraryAdmin'
import { useAdminJob } from './useAdminJob'

type Props = {
  selected: DramaSummary[]
  // Every loaded drama (names for dramas already gone from the selection).
  items: DramaSummary[]
  pc: PcMode
  phone: boolean
  onClear: () => void
  // Phone: leave select mode.
  onDone: () => void
  // Something changed server-side: reload the list.
  onChanged: () => void
  onDeleted: (ids: number[]) => void
}

const translateError = (e: unknown): string | null => {
  if (!(e instanceof ApiError)) return null
  if (e.status === 409) return 'A bulk translation is already running.'
  if (e.status === 403) return "Bulk translate uses paid engines, which this account can't use."
  return null
}

/** The sticky bar shown while at least one drama is selected (desktop), or in select mode (phone). */
export function SelectionBar({ selected, items, pc, phone, onClear, onDone, onChanged, onDeleted }: Props) {
  const [status, setStatus] = useState<LibraryStatus>('translated')
  const [tag, setTag] = useState<LibraryListTag>('Favorite')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [deleting, setDeleting] = useState(false)
  const [skips, setSkips] = useState<string | null>(null)
  const translate = useAdminJob(ADMIN_JOB_IDS.translate)
  const exporter = useAdminJob(ADMIN_JOB_IDS.export, 'export')

  const ids = selected.map((d) => d.id)
  const toTranslate = translatableIds(selected)
  const toExport = exportableIds(selected)
  const local = pc !== 'remote'
  const name = (id: number) => {
    const d = items.find((x) => x.id === id)
    return d ? titleOf(d) : `#${id}`
  }

  const run = <T,>(call: () => Promise<T>, done: (r: T) => string) => {
    setBusy(true)
    setError(null)
    setResult(null)
    call().then(
      (r) => {
        setBusy(false)
        setResult(done(r))
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
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
    setError(null)
    bulkDelete(ids).then(
      (r) => {
        setBusy(false)
        setDeleting(false)
        setResult(describeDeleteResult(r, name))
        onDeleted(r.results.filter((x) => x.ok).map((x) => x.drama_id))
        onChanged()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const n = selected.length
  const translateMsg = translateError(translate.startError)

  const actions = (
    <div className="bar-actions">
      <div className="bar-group">
        <select aria-label="New status" value={status} onChange={(e) => setStatus(e.target.value as LibraryStatus)}>
          {LIBRARY_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetStatus(ids, status), describeBulkResult)}>
          Apply
        </button>
      </div>
      <div className="bar-group">
        <select aria-label="List" value={tag} onChange={(e) => setTag(e.target.value as LibraryListTag)}>
          {LIBRARY_LIST_TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetTag(ids, tag, true), describeBulkResult)}>
          Add
        </button>
        <button type="button" disabled={busy || !n} onClick={() => run(() => bulkSetTag(ids, tag, false), describeBulkResult)}>
          Remove
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
        {local && (
          <button
            type="button"
            disabled={!toExport.length || exporter.active}
            onClick={() => void exporter.start(() => startExport(toExport))}
          >
            Export .zip ({toExport.length})
          </button>
        )}
        {local && (
          <button type="button" className="danger" disabled={busy || !n} onClick={() => setDeleting(true)}>
            Delete…
          </button>
        )}
      </div>
    </div>
  )

  const statusLines = (
    <>
      {!toTranslate.length && n > 0 && <p className="muted" id="translate-needs">{TRANSLATE_NEEDS}</p>}
      {!local && <p className="muted">Delete and export are PC only.</p>}
      <AdminJobLine job={translate} busyText="Translating…" />
      {translateMsg ? <p className="error" role="alert">{translateMsg}</p> : <ErrorBanner error={translate.startError} />}
      {skips && <p className="muted">{skips}</p>}
      <AdminJobLine job={exporter} busyText="Exporting…" artifact="export" showLink={exporter.done} />
      <ErrorBanner error={exporter.startError} describe={{ pcOnly: true, serverText: true }} />
      {deleting && local && (
        <TypedConfirm
          word="DELETE"
          exact
          action={`Delete ${n}`}
          busy={busy}
          onConfirm={remove}
          onCancel={() => setDeleting(false)}
        >
          <p>Permanently deletes {n} drama{n === 1 ? '' : 's'} with their lines and files. No undo.</p>
        </TypedConfirm>
      )}
      <p role="status" data-testid="bulk-result" className={result ? undefined : 'visually-hidden'}>
        {result}
      </p>
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} />
    </>
  )

  if (phone) {
    return (
      <section className="selection-bar phone" aria-label="Selection">
        <div className="bar-row">
          <strong data-testid="selected-count">{n} selected</strong>
          <details className="bar-menu">
            <summary>Actions</summary>
            <div className="bar-menu-body">
              {actions}
              <button type="button" className="link" onClick={onClear}>Clear</button>
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
