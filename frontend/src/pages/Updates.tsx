/*
 * #/updates: everything new in one list, newest first. New chapters of tracked series (the same notifications
 * as Sources > New chapters) and the viewer's followed Live channels that are live or scheduled. Each row is one
 * link: a chapter opens its series in Sources, a channel opens Live. Read-only: dismissing and checking stay
 * where they were, and nothing here is marked read.
 */
import { Paged } from '../components/ShowMore'
import { useCallback, useEffect, useState } from 'react'

import { listFollows } from '../api/liveFollows'
import { listNotifications, listSources, listTracked } from '../api/sources'
import { featureOn } from '../api/features'
import { Badge } from '../components/Badge'
import { ErrorBanner } from '../components/ErrorBanner'
import { PageShell } from '../components/PageShell'
import { buttonClass } from '../components/uiClasses'
import { useFeatures } from '../hooks/useFeatures'
import { routeHref } from '../router'
import type { LiveFollow } from '../types/live'
import type { SourceNotification, SourceSummary, TrackedSeries } from '../types/sources'
import { ago, isoTime } from './sources/sourcesFormat'
import { buildUpdates, type UpdateItem } from './updates/updatesModel'
import './updates/updates.css'

type Loaded = { notifications: SourceNotification[]; tracked: TrackedSeries[]; sources: SourceSummary[]; follows: LiveFollow[] }

function startsText(at: number | null): string {
  if (at === null) return ''
  return ` · starts ${new Date(at * 1000).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`
}

function Row({ item }: { item: UpdateItem }) {
  const href = item.kind === 'chapter' ? routeHref({ name: 'sources', open: item.series }) : routeHref({ name: 'live' })
  return (
    <li>
      <a className="updates-row" href={href} data-testid={`update-${item.key}`}>
        <span className="updates-main">
          <span className="updates-title">{item.title}</span>
          <span className="muted updates-sub">
            {item.kind === 'chapter' ? item.sourceName : `${item.detail}${item.state === 'upcoming' ? startsText(item.startsAt) : ''}`}
            {' · '}
            <time dateTime={isoTime(item.at)}>{ago(item.at)}</time>
          </span>
        </span>
        {item.kind === 'live' ? (
          <Badge tone={item.state === 'live' ? 'accent' : 'info'}>{item.state === 'live' ? 'Live' : 'Scheduled'}</Badge>
        ) : (
          <span className={buttonClass('secondary', 'sm')} aria-hidden="true">Read</span>
        )}
      </a>
    </li>
  )
}

export default function UpdatesPage() {
  const features = useFeatures()
  const sourcesOn = featureOn('sources', features)
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    // One failing list must not hide the other: chapters and channels come from different stores.
    let failure: unknown = null
    const keep = <T,>(fallback: T) => (e: unknown) => {
      failure ??= e
      return fallback
    }
    return Promise.all([
      sourcesOn ? listNotifications().catch(keep([] as SourceNotification[])) : [],
      sourcesOn ? listTracked().catch(keep([] as TrackedSeries[])) : [],
      sourcesOn ? listSources().catch(keep([] as SourceSummary[])) : [],
      listFollows().then((r) => r.items, keep([] as LiveFollow[])),
    ]).then(([notifications, tracked, sources, follows]) => {
      setData({ notifications, tracked, sources, follows })
      setError(failure)
    })
  }, [sourcesOn])

  useEffect(() => {
    void load()
  }, [load])

  const display = (name: string) => data?.sources.find((s) => s.name === name)?.display_name ?? name
  const items = data ? buildUpdates(data.notifications, data.tracked, data.follows, display) : []

  return (
    <PageShell
      width="narrow"
      title="Updates"
      meta="New chapters and channels that are live or scheduled, newest first."
      actions={<button type="button" className={buttonClass('secondary')} onClick={() => void load()}>Refresh</button>}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {data && items.length === 0 && (
        <p className="muted" data-testid="updates-empty">
          Nothing new. Chapters of tracked series and your followed channels show up here.
        </p>
      )}
      {items.length > 0 && (
        <Paged items={items} testId="updates-more">
          {(visible) => <ul className="updates-list">{visible.map((i) => <Row key={i.key} item={i} />)}</ul>}
        </Paged>
      )}
      {!data && <p className="muted" role="status">Loading…</p>}
    </PageShell>
  )
}
