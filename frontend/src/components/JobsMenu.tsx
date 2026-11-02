/*
 * The header Jobs button: a running-count badge (hidden at zero) and a small
 * panel with the active jobs, then the latest finished ones, with Cancel for
 * the jobs the viewer may stop. Permanent delete lives on the Jobs page.
 *
 * The list comes from the shared useJobs hook, the same one the Jobs page
 * reads. Errors are quiet: a failed read keeps the last list, and a refusal
 * (401/403) or a server without the route (404) hides the button.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { api } from '../api/client'
import { cancelJob, forceStopJob } from '../api/jobs'
import { ConfirmButton } from './ConfirmButton'
import { usePopoverDismiss } from '../hooks/usePopoverDismiss'
import { jobsRefusal, useJobs, useNow } from '../hooks/useJobs'
import { cancelsAnyJob, useSession } from '../hooks/useSession'
import { isActive, jobDetail, jobStatusLabel, jobTitledName, plainJobText } from '../pages/diagnosticsFormat'
import { jobLinks } from '../pages/jobs/jobsFilter'
import { routeHref, type Route } from '../router'
import { CANCEL_RULE_NOTE, FORCE_STOP_HINT, cancelHint, offersCancel, offersForceStop, shownStatus, type JobRecord } from '../types/jobs'
import { Badge } from './Badge'
import { statusTone } from './labels'
import { activeCount, badgeText, elapsedText, jobsButtonLabel, menuJobs, timeLeftText } from './jobsMenuState'
import { buttonClass } from './uiClasses'
import './jobsMenu.css'

// A job name opens its stage, or its title when the kind has no stage, or the page the server says a title-less job belongs to, or else the Jobs page.
const jobTarget = (j: JobRecord): Route => {
  const links = jobLinks(j)
  return links.stage?.route ?? links.title ?? { name: 'jobs' }
}

const PANEL_REM = 24 // .jobs-panel width in jobsMenu.css
const GUTTER = 16

function panelFitsLeftwards(el: HTMLElement | null): boolean | null {
  if (!el || typeof window === 'undefined') return null
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const width = Math.min(PANEL_REM * rem, window.innerWidth - 2 * GUTTER)
  return el.getBoundingClientRect().right - width >= GUTTER
}

export function JobsMenu() {
  const anyJob = cancelsAnyJob(useSession())
  const { jobs: loaded, error, reload } = useJobs()
  const jobs = loaded ?? []
  const [open, setOpen] = useState(false)
  const [alignStart, setAlignStart] = useState(false)
  const [titles, setTitles] = useState<ReadonlyMap<number, string>>(new Map())
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  const running = activeCount(jobs)
  // Elapsed times keep counting while the panel shows a running job.
  const now = useNow(open && running > 0)

  // Names of the titles the jobs run on, read once when the panel first opens; a failed read leaves the plain job names.
  const titlesAsked = useRef(false)
  useEffect(() => {
    if (!open || titlesAsked.current) return
    titlesAsked.current = true
    api.listDramas({ progress: 'false' }).then(
      (r) => setTitles(new Map(r.items.map((d) => [d.id, d.title_en || d.title_zh || `#${d.id}`]))),
      () => undefined,
    )
  }, [open])

  // Close on a click elsewhere or Escape, like the bell.
  const closePanel = useCallback(() => setOpen(false), [])
  usePopoverDismiss(open, wrap, button, closePanel)

  if (jobsRefusal(error) !== null) return null
  const shown = menuJobs(jobs)
  const cancellable = (j: JobRecord) => isActive(j.status) && offersCancel(j, anyJob)

  const toggle = () => {
    if (!open) setAlignStart(panelFitsLeftwards(button.current) === false)
    setOpen(!open)
  }
  const cancel = async (id: string, action: (id: string) => Promise<unknown> = cancelJob) => {
    try {
      await action(id)
    } catch {
      // quiet: the list below shows what the server says
    }
    reload()
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
                const name = jobTitledName(j, titles)
                const detail = jobDetail(j)
                return (
                  <li key={j.job_id} className="jobs-item">
                    <p className="jobs-item-name">
                      <a href={routeHref(jobTarget(j))} onClick={() => setOpen(false)}>{name}</a>
                    </p>
                    <div className="jobs-item-meta">
                      <Badge tone={statusTone(shownStatus(j))}>{jobStatusLabel(j)}</Badge>
                      <span>{elapsedText(j, now)}</span>
                      {timeLeftText(j, now) && <span>{timeLeftText(j, now)}</span>}
                      {cancellable(j) && (
                        <button type="button" className={buttonClass('secondary', 'sm', 'jobs-cancel')} aria-label={`Cancel ${name}`} title={cancelHint(j.job_id)} onClick={() => void cancel(j.job_id)}>
                          Cancel
                        </button>
                      )}
                    </div>
                    {isActive(j.status) && offersForceStop(j, anyJob) && (
                      <div className="jobs-item-meta">
                        <ConfirmButton name={name} label="Force stop…" verb="force stop" confirmLabel="Confirm force stop" onConfirm={() => void cancel(j.job_id, forceStopJob)} />
                        <span className="muted jobs-note">{FORCE_STOP_HINT}</span>
                      </div>
                    )}
                    {detail && <span className="muted jobs-note">{plainJobText(detail)}</span>}
                  </li>
                )
              })}
            </ul>
          )}
          {shown.some((j) => isActive(j.status) && !cancellable(j)) && <p className="jobs-note">{CANCEL_RULE_NOTE}</p>}
          <p className="jobs-foot">
            <a href={routeHref({ name: 'jobs' })} onClick={() => setOpen(false)}>
              All jobs
            </a>
          </p>
        </div>
      )}
    </div>
  )
}
