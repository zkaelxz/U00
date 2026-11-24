import { describe, expect, it } from 'vitest'

import type { LiveFollow } from '../../types/live'
import type { SourceNotification, TrackedSeries } from '../../types/sources'
import { buildUpdates } from './updatesModel'

const note = (id: number, series: string, at: number, extra: Partial<SourceNotification> = {}): SourceNotification => ({
  id, source: 'mh', series_id: series, chapter_id: `c${id}`, title: null, created_at: at, dismissed: false, ...extra,
})
const follow = (id: number, extra: Partial<LiveFollow> = {}): LiveFollow => ({
  id, channel: '@a', channel_name: 'Anok', mode: 'notify', auto_check: false, poll_minutes: 60,
  last_checked_at: 100, state: 'live', last_status: '', starts_at: null, live_now: true, stream_title: 'Late chat',
  checking: false, mine: true, created_at: 1, options: {} as LiveFollow['options'], ...extra,
})
const tracked = [{ source: 'mh', series_id: 's1', title: 'Tracked name' }] as TrackedSeries[]
const display = (s: string) => s.toUpperCase()

describe('buildUpdates', () => {
  it('puts chapters and channels in one list, newest first', () => {
    const items = buildUpdates([note(1, 's1', 50), note(2, 's2', 300)], tracked, [follow(1, { last_checked_at: 200 })], display)
    expect(items.map((i) => i.key)).toEqual(['chapter:mh:s2', 'live:1', 'chapter:mh:s1'])
  })

  it('folds a series’ chapters into one row at its newest time and names the count', () => {
    const items = buildUpdates([note(1, 's1', 50), note(2, 's1', 80), note(3, 's1', 60)], tracked, [], display)
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ at: 80, title: 'Tracked name · 3 new chapters', sourceName: 'MH' })
  })

  it('names a single chapter and falls back to the series id for an unknown title', () => {
    const items = buildUpdates([note(1, 'zz', 5)], [], [], display)
    expect(items[0].title).toBe('zz · new chapter')
  })

  it('leaves out dismissed chapters, other people’s channels and channels that are not live or scheduled', () => {
    const items = buildUpdates(
      [note(1, 's1', 5, { dismissed: true })],
      tracked,
      [follow(1, { mine: false }), follow(2, { state: 'not_live' }), follow(3, { state: 'error' }), follow(4, { state: 'upcoming', stream_title: '' })],
      display,
    )
    expect(items.map((i) => i.key)).toEqual(['live:4'])
    expect(items[0]).toMatchObject({ detail: 'Scheduled stream', state: 'upcoming' })
  })
})
