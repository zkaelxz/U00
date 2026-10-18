import { useEffect, useState } from 'react'

import {
  LINK_SCAN_JOB_ID, applyLinkDetails, getLinkScan, probeTitleLink, startLinkScan, type LinkScanRow,
} from '../../api/linkDetails'
import { ErrorBanner } from '../../components/ErrorBanner'
import { workspaceHref } from '../../components/libraryView'
import { buttonClass } from '../../components/uiClasses'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { jobSucceeded } from '../../types/jobs'
import { AdminJobLine } from '../libraryAdmin/AdminJobLine'
import { useAdminJob } from '../libraryAdmin/useAdminJob'
import { linkRows, tickedValues, type LinkField } from '../linkFill'

const ALL: LinkField[] = ['title_zh', 'author', 'release_date', 'series']

/**
 * Library tools > "Check old titles from links": a read-only scan of titles
 * that have a link and a blank original title, date, author or series. Nothing
 * is written until a field is ticked and "Fill ticked" is pressed, and then
 * only while that field is still blank.
 */
export function LinkCheckCard({ pc }: { pc: PcMode }) {
  const pending = usePcPendingNote(pc)
  const job = useAdminJob(LINK_SCAN_JOB_ID)
  const [rows, setRows] = useState<LinkScanRow[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const done = job.done && job.job && jobSucceeded(job.job)
  useEffect(() => {
    if (pc !== 'local' || job.active) return
    getLinkScan().then((r) => setRows(r.titles), () => setRows(null))
  }, [pc, job.active, done])

  if (pc !== 'local') return <p className="muted">{pending}</p>
  return (
    <div className="admin-block">
      <p className="muted">
        Reads the links of titles with a blank original title, stream date, author or series: video links with
        yt-dlp, Sources links from the source itself. Free; three at a time, 10–20 seconds apart. Nothing changes
        until you tick it.
      </p>
      <div className="actions">
        <button type="button" disabled={job.active} onClick={() => void job.start(() => startLinkScan())}>
          Check old titles from links
        </button>
      </div>
      <AdminJobLine job={job} busyText="Checking links…" />
      <ErrorBanner error={job.startError ?? error} onDismiss={() => { job.clearError(); setError(null) }} />
      {rows && !job.active && !rows.length && <p className="muted">No title needs details from its link.</p>}
      {rows && rows.length > 0 && (
        <ul className="tool-list" aria-label="What the links say">
          {rows.map((row) => (
            <LinkCheckRow key={row.drama_id} row={row} onRow={(next) => setRows((all) => all && all.map((r) => (r.drama_id === next.drama_id ? next : r)))} onError={setError} />
          ))}
        </ul>
      )}
    </div>
  )
}

function LinkCheckRow({ row, onRow, onError }: {
  row: LinkScanRow
  onRow: (row: LinkScanRow) => void
  onError: (e: unknown) => void
}) {
  const [ticked, setTicked] = useState<Set<LinkField>>(new Set())
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const items = row.suggested && row.current ? linkRows(row.suggested, row.current, ALL) : []
  const fill = tickedValues(items, ticked)
  const name = row.name || `#${row.drama_id}`

  const toggle = (field: LinkField) =>
    setTicked((s) => {
      const n = new Set(s)
      if (!n.delete(field)) n.add(field)
      return n
    })
  const refresh = () => {
    setBusy(true)
    probeTitleLink(row.drama_id).then(
      (r) => {
        setBusy(false)
        setTicked(new Set())
        onRow({ ...row, current: r.current, suggested: r.suggested, note: r.link.note })
      },
      (e: unknown) => {
        setBusy(false)
        onError(e)
      },
    )
  }
  const apply = () => {
    setBusy(true)
    applyLinkDetails(row.drama_id, fill).then(
      (r) => {
        setBusy(false)
        setResult(r.kept.length ? `Filled ${r.applied.length}; ${r.kept.length} already had text and were kept.` : `Filled ${r.applied.length}.`)
        refresh()
      },
      (e: unknown) => {
        setBusy(false)
        onError(e)
      },
    )
  }

  return (
    <li data-testid="link-check-row">
      <span className="tool-row">
        <a href={workspaceHref(row.drama_id)}>{name}</a> <span className="muted">{row.display_url}</span>
      </span>
      {row.note && <span className="muted">{row.note}</span>}
      {items.length > 0 && (
        <ul className="source-suggestions" aria-label={`What the link says for ${name}`}>
          {items.map((r) => (
            <li key={r.field}>
              {r.mode === 'fill' ? (
                <label className="check">
                  <input type="checkbox" checked={ticked.has(r.field)} onChange={() => toggle(r.field)} />
                  <span><strong>{r.label}:</strong> {r.value}</span>
                </label>
              ) : (
                <span>
                  <strong>{r.label}:</strong> {r.current}
                  {r.mode === 'differs' && <span className="muted"> · Link says: {r.value}</span>}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <span className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !Object.keys(fill).length} onClick={apply}>
          Fill ticked
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={refresh}>
          Refresh details from link
        </button>
        {result && <span role="status" className="muted">{result}</span>}
      </span>
    </li>
  )
}
