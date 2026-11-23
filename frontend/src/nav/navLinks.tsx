/*
 * The registry's rail groups as links, with a row for the open title. The wide
 * screen's rail and the narrow screen's drawer both render this, so the groups,
 * labels and Jobs badge cannot drift apart.
 */
import { useMemo } from 'react'

import { api } from '../api/client'
import { activeCount, badgeText } from '../components/jobsMenuState'
import { useJobs } from '../hooks/useJobs'
import { useLoad } from '../hooks/useLoad'
import { routeHref, type Route } from '../router'
import { navIconPath } from './navIcons'
import { visibleRailGroups, type NavContext } from './navItems'

export function Icon({ d }: { d: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}

// Only drama, reader, Watch and comic routes name a title; the viewers' own pages already show it, so this just labels the rail row.
function openTitleId(route: Route): number | null {
  return route.name === 'drama' || route.name === 'read' || route.name === 'watch' || route.name === 'comic' ? route.id : null
}

// The title row, not Library, is the current page while it shows: one row says where you are, in every mode.
function TitleRow({ id, onNavigate }: { id: number; onNavigate?: () => void }) {
  // useLoad refetches whenever `load` changes, so the loader must be stable per id.
  const load = useMemo(() => () => api.getDrama(id), [id])
  const { data } = useLoad(load, 0)
  const title = data ? data.title_en || data.title_zh || `Title #${id}` : `Title #${id}`
  return (
    <a className="rail-title" href={routeHref({ name: 'drama', id, stage: null })} title={title} data-testid="rail-title" aria-current="page" onClick={onNavigate}>
      <span className="rail-title-mark" aria-hidden="true">›</span>
      <span className="rail-label">{title}</span>
    </a>
  )
}

/** `listed` is the docked open menu, which also shows 0 beside Jobs while nothing runs. */
export function NavGroups({ route, context, collapsed = false, listed = false, onNavigate }: { route: Route; context: NavContext; collapsed?: boolean; listed?: boolean; onNavigate?: () => void }) {
  const titleId = openTitleId(route)
  const running = activeCount(useJobs().jobs ?? [])
  return (
    <nav aria-label="Main">
      {visibleRailGroups(context).map((group) => (
        <div key={group.id} className="rail-group">
          {group.heading && <h2 className="rail-heading">{group.heading}</h2>}
          {group.items.map(({ item, active }) => (
            <div key={item.label} className="rail-entry">
              <a
                href={routeHref(item.target)}
                title={collapsed ? item.label : undefined}
                aria-current={active.includes(route.name) && !(item.label === 'Library' && titleId !== null) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Icon d={navIconPath(item.label)} />
                <span className="rail-label">{item.label}</span>
                {item.badge === 'running-jobs' && running > 0 && (
                  <span className="rail-badge" data-testid="rail-jobs-count">{badgeText(running)}</span>
                )}
                {listed && item.badge === 'running-jobs' && running === 0 && (
                  <span className="rail-zero" data-testid="rail-jobs-zero" aria-hidden="true">0</span>
                )}
              </a>
              {item.label === 'Library' && titleId !== null && <TitleRow key={titleId} id={titleId} onNavigate={onNavigate} />}
            </div>
          ))}
        </div>
      ))}
    </nav>
  )
}
