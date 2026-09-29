import { useCallback, useEffect, useState } from 'react'

import { deleteBugReport, getBugReport, listBugReports } from '../../api/bugReports'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { PC_ONLY_DELETE_NOTE, type PcMode } from '../../hooks/usePcOnly'
import type { BugReportListItem } from '../../types/bugReports'
import { CopyBlock } from './LogSection'
import { useDetailsOpen } from './diagnosticsAdmin'

/** "Bug reports": reports sent with "Report a problem", newest first. Loaded when opened. */
export function BugReportsSection({ pc }: { pc: PcMode }) {
  const [items, setItems] = useState<BugReportListItem[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [shown, setShown] = useState<{ id: number; markdown: string } | null>(null)
  const [note, setNote] = useState('')
  const [openRef, open] = useDetailsOpen()

  const load = useCallback(() => {
    listBugReports().then((r) => {
      setItems(r)
      setError(null)
    }, setError)
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const copy = async (id: number) => {
    setNote('')
    try {
      const r = await getBugReport(id)
      setShown(r)
      try {
        if (!navigator.clipboard?.writeText) throw new Error('no clipboard')
        await navigator.clipboard.writeText(r.markdown)
        setNote(`Copied report #${id}.`)
      } catch {
        setNote(`Report #${id} is shown below; copy it from there.`)
      }
    } catch (e) {
      setError(e)
    }
  }

  const remove = async (id: number) => {
    setBusy(id)
    try {
      await deleteBugReport(id)
      if (shown?.id === id) setShown(null)
      load()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section title="Bug reports" storageKey="diagnostics.bugReports" count={items?.length}
      summary="Sent with Report a problem">
      <div ref={openRef} className="diag-stack" data-testid="bug-reports">
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
        {items === null ? (
          <p className="muted">Loading…</p>
        ) : items.length === 0 ? (
          <p className="muted">No reports yet. Use "Report a problem" at the top of any page.</p>
        ) : (
          <ul className="pkg-list bug-report-list">
            {items.map((r) => (
              <li key={r.id}>
                <div className="bug-report-main">
                  <strong>#{r.id}</strong> {r.summary}
                  <div className="muted">
                    {[r.created_at, r.route, r.mode, r.has_screenshot ? 'screenshot on the PC' : null]
                      .filter(Boolean).join(' · ')}
                  </div>
                </div>
                <div className="actions">
                  <button type="button" aria-label={`Copy report #${r.id}`} onClick={() => void copy(r.id)}>
                    Copy
                  </button>
                  {pc !== 'remote' && (
                    <ConfirmButton name={`report #${r.id}`} busy={busy === r.id}
                      disabled={busy !== null && busy !== r.id} onConfirm={() => void remove(r.id)} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {pc === 'remote' && items && items.length > 0 && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
        <span className="muted" aria-live="polite">{note}</span>
        {shown && <CopyBlock text={shown.markdown} label={`Report #${shown.id}`} />}
      </div>
    </Section>
  )
}
