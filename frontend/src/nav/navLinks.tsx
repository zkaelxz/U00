/*
 * The registry's rail groups as links, with a row for the open title. The wide
 * screen's rail and the narrow screen's drawer both render this, so the groups,
 * labels and Jobs badge cannot drift apart.
 */
import { useMemo } from 'react'

import { api } from '../api/client'
import { Badge } from '../components/Badge'
import { activeCount, badgeText } from '../components/jobsMenuState'
import { useJobs } from '../hooks/useJobs'
import { useLoad } from '../hooks/useLoad'
import { routeHref, type Route } from '../router'
import { visibleRailGroups, type NavContext } from './navItems'

// Feather-style 24px paths, keyed by registry label. Labels stay the accessible names when the rail is collapsed.
const ICONS: Record<string, string> = {
  Library: 'm16 6 4 14M12 6v14M8 8v12M4 4v16',
  'Library tools': 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5z',
  Sources: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  Discover: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36z',
  'Make subtitles': 'M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM7 15h4M15 15h2M7 11h2M13 11h4',
  'Quick translate': 'M4 5h8M8 3v2M5 9c1 3 4 6 7 7M11 5c0 5-3 9-7 11M13 21l4-10 4 10M14.5 18h5',
  Jobs: 'm3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8',
  Live: 'M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3',
  Settings: 'M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0M16 4v4M8 10v4M18 16v4',
  'People and access': 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  Diagnostics: 'M3 12h4l3-8 4 16 3-8h4',
  'Benchmark Lab': 'M10 3v6L4 19a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-6-10V3M9 3h6',
  Assistant: 'M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4z',
}

/** The path for a registry label's icon (Library's for an unknown one). */
export const navIconPath = (label: string): string => ICONS[label] ?? ICONS.Library

export function Icon({ d }: { d: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  )
}

// Only drama, reader and comic routes name a title; the viewers' own pages already show it, so this just labels the rail row.
function openTitleId(route: Route): number | null {
  return route.name === 'drama' || route.name === 'read' || route.name === 'comic' ? route.id : null
}

function TitleRow({ id, onNavigate }: { id: number; onNavigate?: () => void }) {
  // useLoad refetches whenever `load` changes, so the loader must be stable per id.
  const load = useMemo(() => () => api.getDrama(id), [id])
  const { data } = useLoad(load, 0)
  const title = data ? data.title_en || data.title_zh || `Title #${id}` : `Title #${id}`
  return (
    <a className="rail-title" href={routeHref({ name: 'drama', id, stage: null })} title={title} data-testid="rail-title" onClick={onNavigate}>
      <span className="rail-title-mark" aria-hidden="true">›</span>
      <span className="rail-label">{title}</span>
    </a>
  )
}

export function NavGroups({ route, context, collapsed = false, onNavigate }: { route: Route; context: NavContext; collapsed?: boolean; onNavigate?: () => void }) {
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
                aria-current={active.includes(route.name) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Icon d={ICONS[item.label] ?? ICONS.Library} />
                <span className="rail-label">{item.label}</span>
                {item.badge === 'running-jobs' && running > 0 && (
                  <span className="rail-badge" data-testid="rail-jobs-count">
                    <Badge tone="accent">{badgeText(running)}</Badge>
                  </span>
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
