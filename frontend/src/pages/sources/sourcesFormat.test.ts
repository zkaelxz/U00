import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { SourcesSettings, SourceSummary } from '../../types/sources'
import {
  SEARCH_REMOTE_ALLOWED,
  ago,
  cacheLabel,
  describeSourceError,
  paceHelp,
  draftFrom,
  groupChapters,
  errorCategoryLabel,
  healthLine,
  healthTooltip,
  healthText,
  healthTone,
  limitGroups,
  looksLikeUrl,
  pacingErrors,
  pacingSummary,
  pageSummary,
  pausedFor,
  percent,
  profileLine,
  resultsHeader,
  safeHref,
  seriesView,
  type SeriesJobLike,
  searchDisabledReason,
  searchInSummary,
  searchSourcesParam,
  searchableSources,
  selectedSources,
  seriesLinks,
  seriesMeta,
  settingsChanges,
  settingsSummary,
  tierLines,
} from './sourcesFormat'

const src = (name: string, over: Partial<SourceSummary> = {}): SourceSummary => ({
  name,
  display_name: name.toUpperCase(),
  content_types: [],
  languages: [],
  supports: {
    search: true, get_series: true, get_chapters: true, get_pages: false, download_page: false,
    get_chapter_text: false, get_audio_url: false, login: false,
  },
  import_supported: false,
  auth_supported: false,
  supports_adult_toggle: false,
  enabled: true,
  adult_enabled: false,
  health: 'green',
  has_saved_signin: false,
  pace: 'normal',
  fast_allowed: false,
  slowed_down: false,
  ...over,
})

const SETTINGS: SourcesSettings = {
  pace_min_delay: 3, pace_max_delay: 8, max_concurrent: 1, max_retries: 3,
  session_break_min_requests: 8, session_break_max_requests: 20,
  session_break_min_delay: 30, session_break_max_delay: 90,
  cache_mode: 'keep_originals', cache_max_mb: 0, check_interval_hours: 24,
  auto_queue_new_chapters: false, demo_source_enabled: false, extraction_diagnostics: false,
  proxy_configured: false, cache_modes: ['none', 'temporary', 'keep_originals'],
  cache: { entries: 0, bytes: 0 },
}

describe('search form', () => {
  it('stays off for remote viewers until step 133', () => {
    expect(SEARCH_REMOTE_ALLOWED).toBe(false)
  })

  it('spots links', () => {
    expect(looksLikeUrl('https://x')).toBe(true)
    expect(looksLikeUrl('  WWW.example.com')).toBe(true)
    expect(looksLikeUrl('Heaven Official')).toBe(false)
  })

  it('explains a disabled Search in order', () => {
    expect(searchDisabledReason('  ', 2, 2, false)).toBe('Still needed: a title.')
    expect(searchDisabledReason('https://x', 2, 2, false)).toBe('Enter a title, not a link.')
    expect(searchDisabledReason('abc', 0, 0, false)).toBe(
      'Still needed: a source that can search. Turn one on in Source settings.',
    )
    expect(searchDisabledReason('abc', 0, 0, true)).toBe(
      'Still needed: a source that can search. Turn one on in Source settings on the main PC.',
    )
    expect(searchDisabledReason('abc', 2, 0, false)).toBe('Still needed: at least one source.')
    expect(searchDisabledReason('abc', 2, 1, false)).toBeNull()
  })

  it('lists enabled sources that can search, and remembers unticked ones only', () => {
    const list = [src('a'), src('b', { enabled: false }), src('c', { supports: { ...src('c').supports, search: false } }), src('d')]
    expect(searchableSources(list).map((s) => s.name)).toEqual(['a', 'd'])
    // A stale unticked name ("gone") is ignored; a new source starts ticked.
    expect(selectedSources(['a', 'd', 'e'], ['d', 'gone'])).toEqual(['a', 'e'])
  })

  it('sends sources only when not all are ticked', () => {
    expect(searchSourcesParam(['a', 'b'], ['a', 'b'])).toBeUndefined()
    expect(searchSourcesParam(['a', 'b'], ['b'])).toEqual(['b'])
  })

  it('summarises Search in and the results', () => {
    expect(searchInSummary(4, 4)).toBe('All 4 searchable sources')
    expect(searchInSummary(2, 4)).toBe('2 of 4 searchable sources')
    expect(searchInSummary(1, 1)).toBe('All 1 searchable source')
    expect(resultsHeader(12, 2)).toBe('12 results · 2 sources had problems')
    expect(resultsHeader(1, 0)).toBe('1 result')
    expect(percent(0.1)).toBe(' 10%')
    expect(percent(null)).toBe('')
  })
})

describe('describeSourceError', () => {
  it('maps each reason to plain copy', () => {
    const hidden = { status: 400, message: 'x', details: { reason: 'CONTENT_HIDDEN' } }
    expect(describeSourceError(hidden, 'Foo').text).toBe('Foo hides some works. Turn on Adult works in Source settings.')
    expect(describeSourceError(hidden, 'Foo', true).text).toBe(
      'Foo hides some works. Turn on Adult works in Source settings on the main PC.',
    )
    expect(describeSourceError({ status: 400, message: '', details: { reason: 'TOS_PROHIBITED' } }, 'Foo').text).toBe(
      "Foo's terms restrict automated access.",
    )
    expect(describeSourceError({ status: 400, message: '', details: { reason: 'NOT_SUPPORTED' } }, 'Foo').text).toBe(
      "Foo can't do that.",
    )
    expect(describeSourceError({ status: 409, message: '', details: { reason: 'CANCELLED' } }, 'Foo').text).toBe('Stopped.')
  })

  it('offers the page and Try again for a browser check', () => {
    const c = describeSourceError(
      new ApiError(409, { code: 'conflict', message: 'x', details: { handoff: true, open_url: 'https://site.example/p' } }),
      'Foo',
    )
    expect(c).toEqual({
      text: 'Foo showed a browser check. Baihe never gets past these.',
      openUrl: 'https://site.example/p',
      retry: true,
    })
    // Only http(s) links are offered.
    expect(describeSourceError({ status: 409, message: '', details: { handoff: true, open_url: 'javascript:x' } }, 'Foo').openUrl)
      .toBeUndefined()
  })

  it('rounds a pause up to minutes', () => {
    expect(describeSourceError({ status: 503, message: '', details: { retry_after: 240 } }, 'Foo').text).toBe(
      'Foo is paused after repeated failures. Try again in 4 min.',
    )
    expect(describeSourceError({ status: 503, message: '', details: { retry_after: 61 } }, 'Foo').text).toMatch(/in 2 min\.$/)
    expect(describeSourceError({ status: 503, message: '', details: { retry_after: 240 } }, 'Foo').retry).toBeUndefined()
    expect(describeSourceError({ status: 503, message: '', details: { retry_after: null } }, 'Foo').text).toBe(
      "Foo isn't reachable right now.",
    )
  })

  it('shows a safe server sentence, else a fallback', () => {
    expect(describeSourceError({ status: 500, message: 'Parse failed.' }, 'Foo').text).toBe('Parse failed.')
    expect(describeSourceError({ status: 500, message: 'open /home/kae/x failed' }, 'Foo').text).toBe(
      'Foo failed. Details are in the app log.',
    )
    expect(describeSourceError(null, 'Foo').text).toBe('Foo failed. Details are in the app log.')
  })
})

describe('header, health and detail', () => {
  it('counts on and paused sources', () => {
    expect(pageSummary([src('a'), src('b', { health: 'red' }), src('c', { enabled: false })])).toEqual({
      on: '2 sources on · 2 searchable', paused: 1,
    })
    expect(pageSummary([src('a'), src('b', { supports: { ...src('b').supports, search: false } })])).toEqual({
      on: '2 sources on · 1 searchable', paused: 0,
    })
  })

  it('writes health as text', () => {
    expect(healthText('green')).toBe('OK')
    expect(healthText('yellow')).toBe('Failing')
    expect(healthText('red')).toBe('Paused')
    expect([healthTone('green'), healthTone('yellow'), healthTone('red')]).toEqual(['ok', 'warn', 'bad'])
  })

  it('describes access tiers', () => {
    expect(
      tierLines({
        STATIC_HTTP: { tested: true, ok: true, reason: null, detail: null, at: 1 },
        RENDERED_BROWSER: { tested: false, ok: false, reason: null, detail: null, at: null },
        AUTHENTICATED_BROWSER: { tested: true, ok: false, reason: 'LOGIN_REQUIRED', detail: null, at: 1 },
      }),
    ).toEqual(['Static: works', 'Browser: untested', 'Signed-in: failed (login required)'])
  })

  it('writes the health line and pause', () => {
    const now = 10_000 * 1000
    expect(healthLine({
      light: 'yellow', consecutive_failures: 1, last_success: 10_000 - 7200, last_failure: 10_000 - 120,
      last_error_type: 'timeout', last_error: null, last_latency: 1.24, unavailable_until: null, retry_after: null,
    }, now)).toBe('Last success 2 h ago · last failure 2 min ago (timeout) · 1.2 s')
    expect(healthLine({
      light: 'green', consecutive_failures: 0, last_success: null, last_failure: null, last_error_type: null,
      last_error: null, last_latency: null, unavailable_until: null, retry_after: null,
    })).toBe('No requests yet.')
    const failed = {
      light: 'yellow', consecutive_failures: 1, last_success: null, last_failure: 10_000 - 60,
      last_error_type: 'LAYOUT_CHANGED', last_error_category: 'layout_changed', last_error: null,
      last_latency: null, unavailable_until: null, retry_after: null,
    }
    expect(healthLine(failed, now)).toBe('Last failure 1 min ago (layout changed)')
    expect(healthTooltip(failed)).toBe('Error type: LAYOUT_CHANGED')
    expect(healthTooltip({ ...failed, last_error_type: null })).toBeUndefined()
    expect(errorCategoryLabel({ ...failed, last_error_category: 'site_down' })).toBe('site down')
    expect(errorCategoryLabel({ ...failed, last_error_category: 'page_missing' })).toBe('page missing')
    expect(errorCategoryLabel({ ...failed, last_error_category: 'needs_sign_in' })).toBe('needs sign-in')
    expect(errorCategoryLabel({ ...failed, last_error_category: 'domains_unreachable' })).toBe('every known address is unreachable')
    expect(errorCategoryLabel({ ...failed, last_error_category: 'new_thing' })).toBe('failed')
    expect(errorCategoryLabel({ ...failed, last_error_category: null, last_error_type: null })).toBeNull()
    expect(pausedFor(240)).toBe('Paused for another 4 min.')
    expect(pausedFor(0)).toBeNull()
    expect(ago(null)).toBe('never')
    expect(ago(100, 100_000 + 30_000)).toBe('just now')
    expect(ago(0.001, 3 * 86400 * 1000)).toBe('2 d ago')
  })
})

describe('series', () => {
  it('writes the meta line', () => {
    const info = { title: 'T', url: null, cover_url: null, authors: [], description: null, genres: [], status: 'ongoing', content_type: null, language: 'zh' }
    expect(seriesMeta('Foo', info, 124)).toBe('Foo · 124 chapters · Ongoing · Chinese')
    expect(seriesMeta('Foo', null, 1)).toBe('Foo · 1 chapter')
  })

  it('groups chapters and limits across groups', () => {
    const ch = (id: string, group: string) => ({ chapter_id: id, title: id, group, url: '' })
    const groups = groupChapters([ch('1', 'Main'), ch('2', 'Main'), ch('x', 'Extra'), ch('3', 'Main')])
    expect(groups.map((g) => [g.group, g.chapters.map((c) => c.chapter_id)])).toEqual([
      ['Main', ['1', '2', '3']], ['Extra', ['x']],
    ])
    expect(limitGroups(groups, 2).map((g) => g.chapters.length)).toEqual([2])
    expect(limitGroups(groups, 4).map((g) => g.chapters.length)).toEqual([3, 1])
  })
})

describe('settings', () => {
  it('summarises settings and pacing', () => {
    expect(settingsSummary(SETTINGS, [src('a'), src('b', { enabled: false })])).toBe(
      '1 of 2 sources on · 3–8 s gap · cache: Keep originals',
    )
    expect(settingsSummary(null, [src('a')])).toBe('1 of 1 sources on')
    expect(pacingSummary(SETTINGS)).toBe('3–8 s gap · 1 at a time · 3 retries · breaks every 8–20')
    expect(pacingSummary({ ...SETTINGS, session_break_min_requests: 0 })).toMatch(/breaks off$/)
    expect(cacheLabel('keep_both')).toBe('Keep both')
    expect(cacheLabel('odd_mode')).toBe('Odd mode')
  })

  it('sends only the changed keys', () => {
    const d = draftFrom(SETTINGS)
    expect(settingsChanges(SETTINGS, d)).toEqual({})
    expect(settingsChanges(SETTINGS, { ...d, pace_max_delay: '10', cache_mode: 'none', demo_source_enabled: true }))
      .toEqual({ pace_max_delay: 10, cache_mode: 'none', demo_source_enabled: true })
    // "8.0" is the same number.
    expect(settingsChanges(SETTINGS, { ...d, pace_max_delay: '8.0' })).toEqual({})
  })

  it('edits the cache size limit (0 = no limit, whole MB)', () => {
    const d = draftFrom(SETTINGS)
    expect(d.cache_max_mb).toBe('0')
    expect(settingsChanges(SETTINGS, { ...d, cache_max_mb: '500' })).toEqual({ cache_max_mb: 500 })
    expect(pacingErrors({ ...d, cache_max_mb: '-1' }).cache_max_mb).toMatch(/between 0 and/)
    expect(pacingErrors({ ...d, cache_max_mb: '1.5' }).cache_max_mb).toBe('Enter a whole number.')
  })

  it('checks ranges and max >= min', () => {
    const d = draftFrom(SETTINGS)
    expect(pacingErrors(d)).toEqual({})
    expect(pacingErrors({ ...d, pace_max_delay: '2' })).toEqual({ pace_max_delay: 'Must be at least the min.' })
    expect(pacingErrors({ ...d, pace_min_delay: '1' })).toEqual({ pace_min_delay: 'Must be between 3 and 60.' })
    expect(pacingErrors({ ...d, max_concurrent: '' })).toEqual({ max_concurrent: 'Enter a number.' })
    expect(pacingErrors({ ...d, max_retries: '1.5' })).toEqual({ max_retries: 'Enter a whole number.' })
    expect(pacingErrors({ ...d, session_break_max_requests: '5' })).toEqual({
      session_break_max_requests: 'Must be at least the min.',
    })
  })

  it('writes a profile version line', () => {
    expect(profileLine({ version: 3, kind: 'novel', status: 'active', origin: 'ai', failures: 2, last_failure_reason: 'empty' }))
      .toBe('v3 · novel · active · from ai · 2 failures (empty)')
  })
})

describe('seriesView (the job id is per source, so it may hold another series)', () => {
  const open = { source: 'alpha', series_id: 'B', title: 'B' }
  const result = (series_id: string) => ({ kind: 'series' as const, source: 'alpha', series_id, info: null, chapters: [] })
  const job = (over: Partial<SeriesJobLike>): SeriesJobLike => ({
    status: 'idle', result: null, error: null, startedHere: true, startError: null, ...over,
  })

  it('shows a finished result only for the open series', () => {
    expect(seriesView(open, job({ status: 'done', result: result('B') }), 'sources_series_alpha').result?.series_id).toBe('B')
    expect(seriesView(open, job({ status: 'done', result: result('A') }), 'sources_series_alpha')).toMatchObject({
      status: 'idle', result: null,
    })
    expect(seriesView({ ...open, source: 'beta' }, job({ status: 'done', result: result('B') }), 'sources_series_beta').status)
      .toBe('idle')
  })

  it('a 409 for this job means another series is loading', () => {
    const conflict = new ApiError(409, { code: 'conflict', message: 'x', details: { job_id: 'sources_series_alpha' } })
    expect(seriesView(open, job({ status: 'done', result: result('A'), startError: conflict }), 'sources_series_alpha'))
      .toEqual({ status: 'idle', result: null, error: null, busyOther: true })
  })

  it('ignores a failure found on mount, keeps one started here', () => {
    const err = new ApiError(503, { code: 'x', message: 'x' })
    expect(seriesView(open, job({ status: 'error', error: err, startedHere: false }), 'sources_series_alpha').status).toBe('idle')
    expect(seriesView(open, job({ status: 'error', error: err }), 'sources_series_alpha').error).toBe(err)
    expect(seriesView(null, job({ status: 'running' }), null).status).toBe('idle')
    expect(seriesView(open, job({ status: 'running' }), 'sources_series_alpha').status).toBe('running')
  })

  it('a running run for another series (found on load) is busyOther, not shown as loading', () => {
    const other = { source: 'alpha', series_id: 'A' }
    expect(seriesView(open, job({ status: 'running', runningFor: other }), 'sources_series_alpha'))
      .toEqual({ status: 'idle', result: null, error: null, busyOther: true })
    expect(seriesView(open, job({ status: 'running', runningFor: { source: 'alpha', series_id: 'B' } }), 'sources_series_alpha'))
      .toEqual({ status: 'running', result: null, error: null, busyOther: false })
    expect(seriesView(open, job({ status: 'running', runningFor: null }), 'sources_series_alpha').status).toBe('running')
  })

  it('links only http(s)', () => {
    expect(safeHref('https://a.example/x')).toBe('https://a.example/x')
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref(null)).toBeNull()
  })
})

describe('seriesLinks', () => {
  const base = {
    title: 'T', url: null, cover_url: null, authors: [], description: null, genres: [],
    status: null, content_type: null, language: null,
  }
  it('keeps http(s) links, labels empty ones with the URL, drops anything else', () => {
    expect(seriesLinks({
      ...base,
      links: [
        { label: '百度网盘 (Baidu Pan)', url: 'https://pan.baidu.com/s/1abc', password: 'roh1' },
        { label: '', url: 'https://wwasa.lanzoue.com/b0188mxnyb', password: '' },
        { label: 'bad', url: 'javascript:alert(1)', password: '' },
      ],
    })).toEqual([
      { label: '百度网盘 (Baidu Pan)', url: 'https://pan.baidu.com/s/1abc', password: 'roh1' },
      { label: 'https://wwasa.lanzoue.com/b0188mxnyb', url: 'https://wwasa.lanzoue.com/b0188mxnyb', password: '' },
    ])
  })
  it('is empty without links or info', () => {
    expect(seriesLinks({ ...base })).toEqual([])
    expect(seriesLinks(null)).toEqual([])
  })
})

describe('paceHelp', () => {
  it('says why Fast is unavailable, in one line', () => {
    expect(paceHelp(src('a'))).toBe("Fast is off: this site's rules haven't been checked.")
  })
  it('stays quiet when fast is allowed and normal', () => {
    expect(paceHelp(src('a', { fast_allowed: true }))).toBe('')
  })
  it('reports an automatic slowdown first', () => {
    expect(paceHelp(src('a', { slowed_down: true }))).toMatch(/^Slowed down/)
  })
})
