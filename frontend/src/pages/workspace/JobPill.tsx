import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { usePopoverDismiss } from '../../hooks/usePopoverDismiss'
import { cancelsAnyJob, useSession } from '../../hooks/useSession'
import { CANCEL_RULE_NOTE, offersCancel, type JobRecord } from '../../types/jobs'
import { JobPanel } from './stages/JobPanel'
import { pillText, type PillFlash } from './jobPillState'
import { useDramaJobs } from './useDramaJobs'

const FLASH_TEXT: Record<PillFlash, string> = { done: '✓ Done', failed: 'Failed' }

// Progress of what is running on this title, visible on every stage. The
// popover reuses the stage Job panel; the server still decides who may cancel.
export function JobPill({ dramaId, onFinished }: { dramaId: number; onFinished: () => void }) {
  const { active, flash, hidden, reload } = useDramaJobs(dramaId, onFinished)
  const anyJob = cancelsAnyJob(useSession())
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  const closePopover = useCallback(() => setOpen(false), [])
  usePopoverDismiss(open, wrap, button, closePopover)

  // Nothing left to show: the popover would be empty. Its button disappears with it, so
  // focus would fall to the page; the finished flash takes it instead.
  const handOffFocus = useRef(false)
  useEffect(() => {
    if (open && active.length === 0) {
      handOffFocus.current = wrap.current?.contains(document.activeElement) ?? false
      setOpen(false)
    }
  }, [open, active.length])

  const text = active.length > 0 ? pillText(active) : flash ? FLASH_TEXT[flash] : null
  const running = active.length > 0
  // Always mounted: a live region that appears already holding its text is often not announced.
  const announcer = (
    <span className="visually-hidden" role="status">
      {!running && !hidden && flash ? FLASH_TEXT[flash] : ''}
    </span>
  )
  if (hidden || !text) return announcer
  if (!running) {
    return (
      <>
        {announcer}
        <span
          className="job-pill"
          data-testid="job-pill"
          data-state={flash}
          tabIndex={-1}
          ref={(el) => {
            if (el && handOffFocus.current) {
              handOffFocus.current = false
              el.focus()
            }
          }}
        >
          {text}
        </span>
      </>
    )
  }
  const noCancel = active.some((j) => !offersCancel(j, anyJob))

  return (
    <div className="job-pill-wrap" ref={wrap}>
      {announcer}
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
        <div className="job-pill-panel" id={panelId} role="region" aria-label="Running on this title">
          <p className="job-pill-title">Running on this title</p>
          {active.map((j: JobRecord) => (
            <JobPanel key={j.job_id} job={j} pollError={null} canCancel={offersCancel(j, anyJob)} onCancelled={reload} />
          ))}
          {noCancel && <p className="muted job-pill-note">{CANCEL_RULE_NOTE}</p>}
        </div>
      )}
    </div>
  )
}
