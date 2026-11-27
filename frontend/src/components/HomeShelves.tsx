/*
 * The Library's first screen: one Running line. A viewer who can't control the PC queue (a household member) sees
 * their own running titles under "Titles you added" instead, with no buttons they could not use.
 */
import { getHome } from '../api/library'
import { useJobs, useNow } from '../hooks/useJobs'
import { useLoad } from '../hooks/useLoad'
import { routeHref } from '../router'
import type { LibraryHome } from '../types/library'
import { activeCount } from './jobsMenuState'
import { runningLine, stepVerb } from './homeShelfLogic'
import './homeShelves.css'

export function HomeShelves({ reloadKey }: { reloadKey: number }) {
  const { jobs } = useJobs()
  const active = activeCount(jobs ?? [])
  // A step starting or ending moves titles between shelves; the jobs list already hears about it.
  const home = useLoad(getHome, reloadKey * 1000 + active)
  const now = useNow(active > 0)
  if (!home.data) return null
  return (
    <div className="home-shelves" data-testid="home-shelves">
      {home.data.can_control_queue
        ? <RunningRow home={home.data} jobs={jobs} now={now} />
        : <YoursShelf home={home.data} jobs={jobs} now={now} />}
    </div>
  )
}

function RunningRow({ home, jobs, now }: { home: LibraryHome; jobs: ReturnType<typeof useJobs>['jobs']; now: number }) {
  const line = runningLine(home.running, home.running_count, jobs, now)
  if (!line) return null
  return (
    <a className="home-running" href={routeHref({ name: 'jobs' })} data-testid="home-running">
      <span className="home-running-top">
        <span><strong>Running</strong> · {line.text}</span>
        {line.left && <span className="muted">{line.left}</span>}
      </span>
      {line.progress !== null && (
        <span className="home-bar wide" aria-hidden="true"><span style={{ width: `${Math.round(line.progress * 100)}%` }} /></span>
      )}
      <span className="home-running-foot">
        <span className="muted">{line.waiting ?? ''}</span>
        <span className="home-link">Open Jobs</span>
      </span>
    </a>
  )
}

function YoursShelf({ home, jobs, now }: { home: LibraryHome; jobs: ReturnType<typeof useJobs>['jobs']; now: number }) {
  if (home.running.length === 0) return null
  return (
    <section className="home-shelf" aria-labelledby="home-yours">
      <header className="home-shelf-head"><h2 id="home-yours">Titles you added</h2></header>
      <ul className="home-list">
        {home.running.map((e) => {
          const line = runningLine([e], 1, jobs, now)
          return (
            <li key={`r-${e.drama_id}-${e.step}`} className="home-list-row column">
              <span className="home-running-top">
                <span className="home-list-title">{e.title}</span>
                <span className="muted">{e.status === 'running' ? `${stepVerb(e.step)}${line?.left ? ` · ${line.left}` : ''}` : 'Waiting its turn'}</span>
              </span>
              {line?.progress != null && (
                <span className="home-bar wide" aria-hidden="true"><span style={{ width: `${Math.round(line.progress * 100)}%` }} /></span>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
