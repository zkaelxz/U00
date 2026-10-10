/*
 * Danger zone: reset the whole library (PC only). Kept self-contained and
 * mounted with one line in Diagnostics.tsx, so it can be removed if reset
 * becomes CLI-only. No storageKey: always collapsed on load.
 */
import { useEffect, useRef, useState } from 'react'

import { RESET_WORD, resetLibrary } from '../../api/diagnostics'
import { getStats } from '../../api/library'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { TypedConfirm } from '../../components/TypedConfirm'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import type { LibraryDashboard } from '../../types/library'
import { adminErrorText, libraryStatsLine, resetBlockedReason, useDetailsOpen, type AdminBusy } from './diagnosticsAdmin'

export function DangerZone({ pc, jobsActive, busy, onBusy, onReset, onOpenChange }: {
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  onReset: () => void
  onOpenChange: (open: boolean) => void
}) {
  const pending = usePcPendingNote(pc)
  if (pending) {
    return (
      <div className="danger-zone">
        <Section title="Danger zone" summary="Reset library">
          <p className="muted" data-testid="pc-pending">{pending}</p>
        </Section>
      </div>
    )
  }
  if (pc === 'remote') {
    return (
      <div className="danger-zone">
        <Section title="Danger zone" summary={PC_ONLY_SUMMARY}>
          <p className="muted">{PC_ONLY_BODY}</p>
        </Section>
      </div>
    )
  }
  return (
    <div className="danger-zone">
      <Section title="Danger zone" summary="Reset library">
        <ResetBlock jobsActive={jobsActive} busy={busy} onBusy={onBusy} onReset={onReset} onOpenChange={onOpenChange} />
      </Section>
    </div>
  )
}

function ResetBlock({ jobsActive, busy, onBusy, onReset, onOpenChange }: {
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  onReset: () => void
  onOpenChange: (open: boolean) => void
}) {
  const [openRef, open] = useDetailsOpen()
  const [stats, setStats] = useState<LibraryDashboard | null>(null)
  const [statsError, setStatsError] = useState<unknown>(null)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState(false)
  const [formKey, setFormKey] = useState(0)
  const doneRef = useRef<HTMLParagraphElement>(null)
  // Unmounting (e.g. the tab turns remote) stops the page's job polling too.
  useEffect(() => {
    onOpenChange(open)
    return () => onOpenChange(false)
  }, [open, onOpenChange])

  useEffect(() => {
    if (!open) return
    let live = true
    getStats().then(
      (s) => live && setStats(s),
      (e: unknown) => live && setStatsError(e),
    )
    return () => {
      live = false
    }
  }, [open, done])

  useEffect(() => {
    if (done) doneRef.current?.focus()
  }, [done])

  const resetting = busy?.kind === 'reset'
  const blocked = resetBlockedReason(jobsActive, busy)

  const reset = () => {
    onBusy({ kind: 'reset', name: 'library' })
    setError(null)
    resetLibrary().then(
      () => {
        onBusy(null)
        setDone(true)
        setFormKey((k) => k + 1)
        onReset()
      },
      (e: unknown) => {
        onBusy(null)
        setError(e)
      },
    )
  }

  const line = stats ? libraryStatsLine(stats) : null
  return (
    <div ref={openRef} className="diag-stack">
      <h3>Reset library</h3>
      {done && (
        <p ref={doneRef} tabIndex={-1} role="status" data-testid="reset-done">
          Library reset. <a href={routeHref({ name: 'library' })}>Go to Library</a>
        </p>
      )}
      <ErrorBanner error={statsError} onDismiss={() => setStatsError(null)} />
      {!stats ? (
        !statsError && <p className="muted">Loading…</p>
      ) : line === null ? (
        !done && <p className="muted">Library is already empty.</p>
      ) : (
        <>
          <p>{line}</p>
          <TypedConfirm
            key={formKey}
            word={RESET_WORD}
            exact
            action="Reset library"
            busy={resetting}
            blocked={blocked && jobsActive ? (
              <>{blocked} <a href={routeHref({ name: 'jobs' })}>Open Jobs</a></>
            ) : blocked}
            onConfirm={reset}
          >
            <p>Deletes every drama, line, glossary, series, progress and file. No undo.</p>
          </TypedConfirm>
          {error != null && (
            <p className="error" role="alert">
              {adminErrorText(error, 'reset')}
            </p>
          )}
        </>
      )}
    </div>
  )
}
