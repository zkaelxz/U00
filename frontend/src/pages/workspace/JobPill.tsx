import { useEffect, useId, useRef, useState } from 'react'

import { REMOTE_ADMIN_NOTE, isRemoteAdmin, useSession } from '../../hooks/useSession'
import { offersCancel, type JobRecord } from '../../types/jobs'
import { JobPanel } from './stages/JobPanel'
import { pillText, type PillFlash } from './jobPillState'
import { useDramaJobs } from './useDramaJobs'

const FLASH_TEXT: Record<PillFlash, string> = { done: '✓ Done', failed: 'Failed' }

// Progress of what is running on this title, visible on every stage. The
// popover reuses the stage Job panel; the server still decides who may cancel.
export function JobPill({ dramaId, onFinished }: { dramaId: number; onFinished: () => void }) {
  const { active, flash, hidden, reload } = useDramaJobs(dramaId, onFinished)
  const remoteAdmin = isRemoteAdmin(useSession())
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        if (e.key !== 'Escape') return
        setOpen(false)
        button.current?.focus()
      } else if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])

  // Nothing left to show: the popover would be empty.
  useEffect(() => {
    if (open && active.length === 0) setOpen(false)
  }, [open, active.length])

  const text = active.length > 0 ? pillText(active) : flash ? FLASH_TEXT[flash] : null
  if (hidden || !text) return null
  const running = active.length > 0
  if (!running) {
    return (
      <span className="job-pill" data-testid="job-pill" data-state={flash} role="status">
        {text}
      </span>
    )
  }
  const noCancel = active.some((j) => !offersCancel(j, remoteAdmin))

  return (
    <div className="job-pill-wrap" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="job-pill"
        data-testid="job-pill"
        data-state="running"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden="true">● </span>
        {text}
      </button>
      {open && (
        <div className="job-pill-panel" id={panelId} role="region" aria-label="Running on this drama">
          <p className="job-pill-title">Running on this drama</p>
          {active.map((j: JobRecord) => (
            <JobPanel key={j.job_id} job={j} pollError={null} canCancel={offersCancel(j, remoteAdmin)} onCancelled={reload} />
          ))}
          {noCancel && <p className="muted job-pill-note">{REMOTE_ADMIN_NOTE} That includes cancelling their jobs.</p>}
        </div>
      )}
    </div>
  )
}
