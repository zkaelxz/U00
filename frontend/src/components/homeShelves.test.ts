import { describe, expect, it } from 'vitest'

import type { JobRecord } from '../types/jobs'
import type { LibraryNeedsYouEntry, LibraryReadyEntry, LibraryRunningEntry } from '../types/library'
import { formatLength, kindTag, needsRow, readyCard, readyOrder, runningLine, timeLeft } from './homeShelves'

const ready = (over: Partial<LibraryReadyEntry> = {}): LibraryReadyEntry => ({
  drama_id: 4, title: 'Chion: karaoke', media_type: 'streamer_vod', content_mode: 'streamer_vod', has_cover_art: false,
  updated_at: null, read_percent: 0, length_seconds: 7500, page_count: 0, ...over,
})

const needs = (over: Partial<LibraryNeedsYouEntry>): LibraryNeedsYouEntry => ({
  drama_id: 7, title: 'Anok collab', media_type: 'streamer_vod', content_mode: 'streamer_vod', has_cover_art: false,
  updated_at: null, kind: 'failed', step: 'translate', reason: null, risk_count: null, series_id: null, hold_kind: null, ...over,
})

const admin = { canControlQueue: true, canOpenSettings: true, canEditLines: true }
const member = { canControlQueue: false, canOpenSettings: false, canEditLines: false }

describe('formatLength', () => {
  it('reads hours with padded minutes and never says 0 min', () => {
    expect(formatLength(7500)).toBe('2 h 05 m')
    expect(formatLength(2700)).toBe('45 min')
    expect(formatLength(5)).toBe('1 min')
  })
})

describe('readyCard', () => {
  it('offers Watch with the length for a new video, and the Watch page as the link', () => {
    const card = readyCard(ready(), 0)
    expect(card).toMatchObject({ label: 'Watch · 2 h 05 m', progress: null })
    expect(card.href).toContain('watch')
  })
  it('continues where the Watch page stopped, with the progress', () => {
    const card = readyCard(ready(), 4360)
    expect(card.label).toBe('Continue at 1:12:40')
    expect(card.progress).toBeCloseTo(4360 / 7500)
  })
  it('counts a comic in pages and continues one already started', () => {
    const comic = ready({ media_type: 'manhua', content_mode: null, length_seconds: null, page_count: 24 })
    expect(readyCard(comic, 0).label).toBe('Read · 24 pages')
    expect(readyCard({ ...comic, read_percent: 50 }, 0)).toMatchObject({ label: 'Continue reading', progress: 0.5 })
  })
  it('puts started cards first and keeps the rest in order', () => {
    const order = readyOrder([{ id: 1, progress: null }, { id: 2, progress: 0.4 }, { id: 3, progress: null }])
    expect(order.map((c) => c.id)).toEqual([2, 1, 3])
  })
})

describe('kindTag', () => {
  it('names a stream, a comic and a novel', () => {
    expect(kindTag({ media_type: 'streamer_vod', content_mode: null })).toBe('Stream')
    expect(kindTag({ media_type: 'manga', content_mode: null })).toBe('Comic')
    expect(kindTag({ media_type: null, content_mode: 'novel_narration' })).toBe('Novel')
  })
})

describe('needsRow', () => {
  it('sends a refused key to Settings at the PC, and elsewhere to Jobs', () => {
    const e = needs({ reason: 'The Claude key was refused.' })
    expect(needsRow(e, admin)).toMatchObject({ text: 'Translating stopped: The Claude key was refused.', action: { label: 'Fix the key' } })
    expect(needsRow(e, { ...admin, canOpenSettings: false })?.action?.label).toBe('See what happened')
  })
  it('tells a member the owner was told, with no button', () => {
    expect(needsRow(needs({ reason: 'boom' }), member)).toEqual({ text: 'Stopped. The PC owner has been told.', action: null })
  })
  it('names episode 1 for a series paused after it, and opens the series otherwise', () => {
    const hold = needs({ kind: 'series_hold', drama_id: null, series_id: 3, reason: 'Episode 1 is done.', hold_kind: 'review' })
    expect(needsRow(hold, admin)).toMatchObject({ text: 'Episode 1 is done.', action: { label: 'See episode 1' } })
    expect(needsRow({ ...hold, hold_kind: 'ceiling' }, admin)?.action?.label).toBe('Open the series')
  })
  it('offers a check only to someone who can edit lines', () => {
    const check = needs({ kind: 'check', risk_count: 7 })
    expect(needsRow(check, admin)).toMatchObject({ text: 'You can watch it now. 7 lines look unsure', action: { label: 'Check 7 lines' } })
    expect(needsRow(check, { ...admin, canEditLines: false })).toBeNull()
    expect(needsRow(check, admin)?.action?.href).toContain('watch')
    expect(needsRow({ ...check, media_type: 'novel', content_mode: 'novel' }, admin)?.action?.href).toContain('review')
  })
})

describe('runningLine', () => {
  const entry = (over: Partial<LibraryRunningEntry>): LibraryRunningEntry => ({
    drama_id: 1, title: 'Chion: game night', media_type: 'streamer_vod', content_mode: 'streamer_vod', has_cover_art: false,
    updated_at: null, step: 'transcribe', status: 'running', job_id: 'transcribe_1', started_at: null, ...over,
  })
  it('shows the running step with its progress, time left and how many wait', () => {
    const jobs = [{ job_id: 'transcribe_1', progress: 0.5, started_at: 1000 } as JobRecord]
    const line = runningLine([entry({ status: 'waiting', drama_id: 2, title: 'Other' }), entry({})], 2, jobs, 1600)
    expect(line).toEqual({ text: 'Transcribing Chion: game night', progress: 0.5, left: '10 min left', waiting: '1 more is waiting its turn' })
  })
  it('is null when nothing runs or waits', () => {
    expect(runningLine([], 0, [], 0)).toBeNull()
  })
  it('gives no time left too early to tell', () => {
    expect(timeLeft(0.01, 0, 100)).toBeNull()
  })
})
