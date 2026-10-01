/*
 * The header Jobs button: a running-count badge (hidden at zero) and a small
 * panel with the active jobs, then the latest finished ones, with Cancel for
 * the jobs the viewer may stop. Permanent delete stays on Diagnostics.
 *
 * Job changes arrive on the shared push stream; GET /api/jobs is read on
 * mount, after each (re)connect and when the window regains focus. Only while
 * the stream is down does it poll, and only while the tab is visible.
 * Errors are quiet: a failed read keeps the last list, and a refusal
 * (401/403) or a server without the route (404) hides the button.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { cancelJob, listJobs } from '../api/jobs'
import { useEventStream } from '../hooks/useEventStream'
import { REMOTE_ADMIN_NOTE, isRemoteAdmin, useSession } from '../hooks/useSession'
import { isActive, jobDetail, statusLabel, upsertJob } from '../pages/diagnosticsFormat'
import { offersCancel, type JobRecord } from '../types/jobs'
import { Badge } from './Badge'
import { statusTone } from './labels'
import { HIDE_ON, JOBS_POLL_MS, activeCount, badgeText, elapsedText, jobsButtonLabel, menuJobs } from './jobsMenuState'
import { writeSectionOpen } from './sectionStorage'
import { buttonClass } from './uiClasses'
import './jobsMenu.css'

const PANEL_REM = 24 // .jobs-panel width in jobsMenu.css
const GUTTER = 16

function panelFitsLeftwards(el: HTMLElement | null): boolean | null {
  if (!el || typeof window === 'undefined') return null
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const width = Math.min(PANEL_REM * rem, window.innerWidth - 2 * GUTTER)
  return el.getBoundingClientRect().right - width >= GUTTER
}

export function JobsMenu() {
  const remoteAdmin = isRemoteAdmin(useSession())
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [hidden, setHidden] = useState(false)
  const [open, setOpen] = useState(false)
  const [alignStart, setAlignStart] = useState(false)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const hiddenRef = useRef(false)
  const panelId = useId()

  const stream = useEventStream((type, data) => {
    const pushed = data as Partial<JobRecord> | null
    if (hiddenRef.current || !pushed?.job_id || (type !== 'job' && type !== 'job_gone')) return
    setJobs((cur) => (type === 'job' ? upsertJob(cur, pushed as JobRecord) : cur.filter((j) => j.job_id !== pushed.job_id)))
    setNow(Date.now() / 1000)
  })
  const polling = stream.mode === 'poll'

  const load = useCallback(() => {
    if (hiddenRef.current) return
    listJobs().then(
      (r) => {
        setJobs(Array.isArray(r?.items) ? r.items : [])
        setNow(Date.now() / 1000)
      },
      (e: unknown) => {
        if (e instanceof ApiError && HIDE_ON.includes(e.status)) {
          hiddenRef.current = true
          setHidden(true)
        }
      },
    )
  }, [])

  useEffect(() => {
    load()
    const timer = polling
      ? window.setInterval(() => {
          if (document.visibilityState !== 'hidden') load()
        }, JOBS_POLL_MS)
      : undefined
    window.addEventListener('focus', load)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', load)
    }
  }, [load, polling, stream.syncs])

  const running = activeCount(jobs)
  // Elapsed times keep counting while the panel shows a running job.
  useEffect(() => {
    if (!open || running === 0) return
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(t)
  }, [open, running])

  // Close on a click elsewhere or Escape, like the bell.
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

  if (hidden) return null
  const shown = menuJobs(jobs)
  const cancellable = (j: JobRecord) => isActive(j.status) && offersCancel(j, remoteAdmin)

  const toggle = () => {
    if (!open) setAlignStart(panelFitsLeftwards(button.current) === false)
    setOpen(!open)
  }
  const cancel = async (id: string) => {
    try {
      await cancelJob(id)
    } catch {
      // quiet: the list below shows what the server says
    }
    load()
  }
  // The Jobs fold on Diagnostics is remembered per viewer; open it for this visit.
  const allJobs = () => {
    try {
      writeSectionOpen(window.localStorage, 'diagnostics.jobs', true)
    } catch {
      // storage unavailable: the fold keeps its own default
    }
    setOpen(false)
  }

  return (
    <div className="jobs-menu" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="jobs-menu-btn"
        aria-label={jobsButtonLabel(running)}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={toggle}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
        </svg>
        {running > 0 && (
          <span className="jobs-count" aria-hidden="true" data-testid="jobs-count">
            <Badge tone="accent">{badgeText(running)}</Badge>
          </span>
        )}
      </button>
      {open && (
        <div className={alignStart ? 'jobs-panel align-start' : 'jobs-panel'} id={panelId} role="region" aria-labelledby={`${panelId}-title`}>
          <p className="jobs-panel-title" id={`${panelId}-title`}>
            Jobs
          </p>
          {shown.length === 0 ? (
            <p className="muted jobs-empty">No jobs yet.</p>
          ) : (
            <ul className="jobs-list">
              {shown.map((j) => {
                const name = j.description || j.job_id
                const detail = jobDetail(j)
                return (
                  <li key={j.job_id} className="jobs-item">
                    <p className="jobs-item-name">{name}</p>
                    <div className="jobs-item-meta">
                      <Badge tone={statusTone(j.status)}>{statusLabel(j.status)}</Badge>
                      <span>{elapsedText(j, now)}</span>
                      {cancellable(j) && (
                        <button type="button" className={buttonClass('secondary', 'sm', 'jobs-cancel')} aria-label={`Cancel ${name}`} onClick={() => void cancel(j.job_id)}>
                          Cancel
                        </button>
                      )}
                    </div>
                    {detail && <span className="muted jobs-note">{detail}</span>}
                  </li>
                )
              })}
            </ul>
          )}
          {shown.some((j) => isActive(j.status) && !cancellable(j)) && <p className="jobs-note">{REMOTE_ADMIN_NOTE} That includes cancelling their jobs.</p>}
          <p className="jobs-foot">
            <a href="#/diagnostics" onClick={allJobs}>
              All jobs
            </a>
          </p>
        </div>
      )}
    </div>
  )
}
