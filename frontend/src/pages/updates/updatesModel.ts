// What the Updates page lists: new chapters and followed channels that are live or scheduled, newest first.
// No React here, so the ordering and wording are unit-tested in updatesModel.test.ts.
import { followName } from '../../api/liveFollows'
import type { LiveFollow } from '../../types/live'
import type { OpenSeries, SourceNotification, TrackedSeries } from '../../types/sources'

export type UpdateItem =
  | { kind: 'chapter'; key: string; at: number; title: string; sourceName: string; series: OpenSeries }
  | { kind: 'live'; key: string; at: number; title: string; state: 'live' | 'upcoming'; detail: string; startsAt: number | null }

/** Same wording as the Live page's followed-channels row, minus the bits that need a clock. */
function liveDetail(f: Pick<LiveFollow, 'state' | 'stream_title'>): string {
  const title = f.stream_title.trim()
  if (f.state === 'live') return title ? `Live now: ${title}` : 'Live now'
  return title ? `Scheduled: ${title}` : 'Scheduled stream'
}

/**
 * `follows` may hold other people's channels (the PC owner is shown everyone's on the Live page); an Updates
 * page is about what is new for the viewer, so only their own count here.
 * Several unseen chapters of one series fold into one row, so a long series can't push every channel off the page.
 */
export function buildUpdates(
  notifications: SourceNotification[],
  tracked: TrackedSeries[],
  follows: LiveFollow[],
  display: (source: string) => string,
): UpdateItem[] {
  const items: UpdateItem[] = []
  const bySeries = new Map<string, { n: SourceNotification; count: number }>()
  for (const n of notifications) {
    if (n.dismissed) continue
    const key = `${n.source}:${n.series_id}`
    const seen = bySeries.get(key)
    if (!seen) bySeries.set(key, { n, count: 1 })
    else {
      seen.count += 1
      if (n.created_at > seen.n.created_at) seen.n = n
    }
  }
  for (const [key, { n, count }] of bySeries) {
    const title = n.title || tracked.find((t) => t.source === n.source && t.series_id === n.series_id)?.title || n.series_id
    items.push({
      kind: 'chapter',
      key: `chapter:${key}`,
      at: n.created_at,
      title: count > 1 ? `${title} · ${count} new chapters` : `${title} · new chapter`,
      sourceName: display(n.source),
      series: { source: n.source, series_id: n.series_id, title },
    })
  }
  for (const f of follows) {
    if (!f.mine || (f.state !== 'live' && f.state !== 'upcoming')) continue
    items.push({
      kind: 'live',
      key: `live:${f.id}`,
      at: f.last_checked_at ?? f.created_at,
      title: followName(f),
      state: f.state,
      detail: liveDetail(f),
      startsAt: f.starts_at ?? null,
    })
  }
  return items.sort((a, b) => b.at - a.at || a.key.localeCompare(b.key))
}
