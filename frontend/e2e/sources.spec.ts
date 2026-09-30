import { expect, test } from '@playwright/test'

import { SERIES_LINKS, SETTINGS, mockSources, posted, searchResult } from './sourcesMocks'

// Sources page (#/sources), desktop. Every search/series/job/settings write
// is mocked (sourcesMocks.ts); nothing here reaches a real site.

test('nav, header, empty-state and disabled reasons', async ({ page }) => {
  const s = await mockSources(page)
  await page.goto('/#/sources')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { level: 2, name: 'Sources' })).toBeVisible()
  await expect(page.getByTestId('sources-summary')).toHaveText('3 sources on · 2 searchable · 1 paused')
  await expect(page.getByTestId('sources-summary').locator('.pill-warn')).toHaveText('1 paused')
  // One card, one input at a time: Search by title first, Paste a link one tap away.
  await expect(page.getByRole('radio', { name: 'Search by title' })).toBeChecked()
  await expect(page.getByRole('textbox', { name: 'Paste a link' })).toBeHidden()
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await expect(page.getByRole('textbox', { name: 'Paste a link' })).toBeVisible()
  await expect(page.getByRole('searchbox', { name: 'Title' })).toBeHidden()
  await page.getByRole('radio', { name: 'Search by title' }).check()

  const search = page.getByRole('button', { name: 'Search', exact: true })
  await expect(search).toBeDisabled()
  await expect(page.getByText('Still needed: a title.')).toBeVisible()

  await page.getByRole('searchbox', { name: 'Title' }).fill('https://x')
  await expect(page.getByText('Enter a title, not a link.')).toBeVisible()
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  expect(posted(s, '/api/sources/search')).toHaveLength(0)

  // Search in: untick both searchable sources.
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByText('Search in').click()
  await page.getByRole('switch', { name: 'Alpha Comics' }).click()
  await page.getByRole('switch', { name: 'Beta Novels' }).click()
  await expect(page.getByRole('switch', { name: 'Beta Novels' })).not.toBeChecked()
  await expect(page.getByText('Still needed: at least one source.')).toBeVisible()
  await page.getByRole('switch', { name: 'Beta Novels' }).click()
  await expect(page.getByRole('switch', { name: 'Beta Novels' })).toBeChecked()
  await expect(search).toBeEnabled()

  // No New chapters panel with nothing tracked; no Import/Track anywhere.
  await expect(page.getByRole('region', { name: 'New chapters' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Import|Track$/ })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('search: running line, cancel, results, per-source errors, series', async ({ page }) => {
  const s = await mockSources(page, { tracked: [{ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1', url: '', drama_id: null, last_checked: null, last_check_error: null }] })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  await expect(page.getByText(/Searching… 10%/)).toBeVisible()
  await expect(page.getByText('Each source is paced, so this can take a minute.')).toBeVisible()
  // All sources ticked: `sources` is left out.
  expect(posted(s, '/api/sources/search')[0].body).toEqual({ query: 'Heaven' })

  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(posted(s, '/api/jobs/sources_search/cancel')).toHaveLength(1)
  await expect(page.getByText('3 results · 2 sources had problems')).toBeVisible()
  await expect(page.getByText('Beta Novels is paused after repeated failures. Try again in 4 min.')).toBeVisible()
  await expect(page.getByText('Alpha Comics hides some works. Turn on Adult works in Source settings.')).toBeVisible()
  await expect(page.locator('img')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Import|^Track/ })).toHaveCount(0)

  // Open a series: title, chapter count, focus on the heading.
  const opener = page.getByRole('button', { name: 'Open on Alpha Comics' }).first()
  await opener.click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByRole('heading', { level: 3, name: 'Heaven Book 1' })).toBeFocused()
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  expect(posted(s, '/api/sources/alpha/series')[0].body).toEqual({ series_id: 'a0' })
  await expect(panel.getByText('Tracked', { exact: true })).toBeVisible()
  // 100 shown first; the last group appears with Show all.
  await expect(panel.getByRole('heading', { level: 4, name: 'Main' })).toBeVisible()
  await expect(panel.getByRole('heading', { level: 4, name: 'Extras' })).toHaveCount(0)
  await panel.getByRole('button', { name: 'Show all 124' }).click()
  await expect(panel.getByRole('heading', { level: 4, name: 'Extras' })).toBeVisible()
  await expect(panel.getByRole('link', { name: 'Open on site ↗' })).toHaveAttribute('rel', 'noopener noreferrer')
  // Results stay visible next to the series on desktop.
  await expect(page.getByTestId('search-results')).toBeVisible()
  await expect(page.locator('img')).toHaveCount(0)

  // Close returns focus to the opener.
  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(panel).toHaveCount(0)
  await expect(opener).toBeFocused()
  expect(s.unmocked).toEqual([])
})

test('series: posted download links are listed to open, never fetched', async ({ page }) => {
  const s = await mockSources(page, { seriesLinks: SERIES_LINKS })
  const lockers: string[] = []
  page.on('request', (r) => {
    if (/baidu|lanzou/.test(r.url())) lockers.push(r.url())
  })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).first().click()
  const links = page.getByRole('region', { name: 'Series' }).getByRole('group', { name: 'Download links' })
  await expect(links.getByText('the app never downloads from them')).toBeVisible()
  await expect(links.getByText('Workspace → Source → Novel text → Attach EPUB', { exact: false })).toBeVisible()
  const baidu = links.getByRole('link', { name: '百度网盘 (Baidu Pan) ↗' })
  await expect(baidu).toHaveAttribute('href', 'https://pan.baidu.com/s/1UW8fzsl6WfJ1RRIXRt_MPw')
  await expect(baidu).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(baidu).toHaveAttribute('target', '_blank')
  await expect(links.getByText('roh1')).toBeVisible()
  await expect(links.getByRole('link', { name: '蓝奏云 (Lanzou) ↗' })).toBeVisible()
  await expect(links.getByText('Code')).toHaveCount(1)
  expect(lockers).toEqual([])
  expect(s.unmocked).toEqual([])
})

test('a 409 on start reattaches; a done job survives a reload; Clear empties', async ({ page }) => {
  const s = await mockSources(page)
  await page.route(/\/api\/sources\/search$/, (route) => {
    s.search = 'running'
    return route.fulfill({
      status: 409, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'conflict', message: 'A request like this is already running.', details: { job_id: 'sources_search' } } }),
    })
  })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByText(/Searching…/)).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: 'already' })).toHaveCount(0)

  s.search = 'done'
  s.searchBody = { ...searchResult(0), errors: {} }
  await expect(page.getByText('No results for "Heaven".')).toBeVisible()

  s.searchBody = { ...searchResult(35), errors: {} }
  await page.reload()
  await expect(page.getByText('35 results', { exact: true })).toBeVisible()
  await expect(page.locator('ul.source-results > li')).toHaveCount(30)
  await page.getByRole('button', { name: 'Show 5 more' }).click()
  await expect(page.locator('ul.source-results > li')).toHaveCount(35)
  await page.getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByTestId('search-results')).toHaveCount(0)
})

test('new chapters: dismiss and two-step stop tracking', async ({ page }) => {
  const tracked = [{ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1', url: '', drama_id: null, last_checked: 1, last_check_error: null },
    { source: 'beta', series_id: 'b0', title: 'Old Book', url: '', drama_id: null, last_checked: 1, last_check_error: 'Timed out.' }]
  const s = await mockSources(page, {
    tracked,
    notifications: [{ id: 7, source: 'alpha', series_id: 'a0', chapter_id: 'c125', title: 'Chapter 125', created_at: Date.now() / 1000 - 7200, dismissed: false }],
  })
  await page.route(/\/api\/sources\/notifications\/7\/dismiss$/, (route) => {
    s.calls.push({ method: 'POST', path: '/api/sources/notifications/7/dismiss', body: null })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ id: 7, dismissed: true }) })
  })
  await page.route(/\/api\/sources\/tracked$/, (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(tracked) })
    s.calls.push({ method: 'POST', path: '/api/sources/tracked', body: route.request().postDataJSON() })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify([tracked[0]]) })
  })
  await page.goto('/#/sources')
  const box = page.getByRole('region', { name: 'New chapters' })
  await expect(box.getByText('Chapter 125 · Alpha Comics · 2 h ago')).toBeVisible()
  await expect(box.getByText('· last check failed: Timed out.')).toBeVisible()

  // Open from New chapters, then Close: focus goes back to that Open button.
  const openBtn = box.getByRole('button', { name: 'Open', exact: true })
  await openBtn.click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(panel).toHaveCount(0)
  await expect(openBtn).toBeFocused()

  await box.getByRole('button', { name: 'Dismiss' }).click()
  await expect(box.getByText('Chapter 125 · Alpha Comics')).toHaveCount(0)

  await box.getByRole('button', { name: 'Stop tracking Old Book' }).click()
  expect(posted(s, '/api/sources/tracked')).toHaveLength(0)
  await box.getByRole('button', { name: 'Confirm stop tracking Old Book' }).click()
  await expect(box.getByText('Old Book')).toHaveCount(0)
  expect(posted(s, '/api/sources/tracked')[0].body).toEqual({ source: 'beta', series_id: 'b0', tracked: false })
  expect(s.unmocked).toEqual([])
})

test('remote: search and settings are PC only, no settings request', async ({ page }) => {
  const s = await mockSources(page, { local: false })
  await page.goto('/#/sources')
  await expect(page.getByText('Searching sources is PC only for now.')).toBeVisible()
  await expect(page.getByRole('search')).toHaveCount(0)
  await page.getByText('Source settings').click()
  await expect(page.getByText('Run this on the main PC.')).toBeVisible()
  await page.waitForTimeout(300)
  expect(s.calls.filter((c) => c.path.startsWith('/api/sources/settings'))).toEqual([])
  expect(s.unmocked).toEqual([])
})

test('series B on the same source while A is still running never shows A', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(3), errors: {} }, seriesHold: true })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  // The mocked start sets the search running; finishing it before that
  // request is handled would leave it running forever. ("Searching…" shows
  // while the start is still in flight, so it is not the signal.)
  await expect.poll(() => posted(s, '/api/sources/search').length).toBe(1)
  s.search = 'done'
  await expect(page.getByText('3 results', { exact: true })).toBeVisible()

  // A (a0) starts and keeps running. A double click starts it once.
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).nth(0).dblclick()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText(/Loading the series/)).toBeVisible()
  expect(posted(s, '/api/sources/alpha/series')).toHaveLength(1)

  // Opening A again while it loads just goes to it: no new start, progress kept.
  await page.getByRole('searchbox', { name: 'Title' }).focus()
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).nth(0).click()
  await expect(panel.getByRole('heading', { level: 3, name: 'Heaven Book 1' })).toBeFocused()
  await expect(panel.getByText(/Loading the series/)).toBeVisible()
  await expect(panel.getByText(/Another series/)).toHaveCount(0)
  expect(posted(s, '/api/sources/alpha/series')).toHaveLength(1)

  // B (a1) on the same source: the server says 409 (its job id is per source).
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).nth(1).click()
  await expect(panel.getByRole('heading', { level: 3, name: 'Heaven Book 2' })).toBeVisible()
  await expect(panel.getByText('Another series from Alpha Comics is still loading.')).toBeVisible()
  await expect(panel.getByText(/Loading the series/)).toHaveCount(0)

  // A finishes on the server: its chapters must not appear under B. Wait
  // for the page to receive A's finished result and render, not a fixed time.
  const aDone = page.waitForResponse(async (r) =>
    r.url().endsWith('/api/sources/jobs/sources_series_alpha/result') && (await r.json()).status === 'done')
  s.seriesHold = false
  s.series = 'done'
  await aDone
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 0))))
  await expect(panel.getByText(/124 chapters/)).toHaveCount(0)
  await expect(panel.getByRole('heading', { level: 3, name: 'Heaven Book 2' })).toBeVisible()

  // Cancel the other one, then Try again loads B.
  s.series = 'running'
  s.seriesHold = true
  await panel.getByRole('button', { name: 'Cancel it' }).click()
  await expect.poll(() => posted(s, '/api/jobs/sources_series_alpha/cancel').length).toBe(1)
  await expect(panel.getByText('Asked the other series to stop. Try again in a moment.')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Cancel it' })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'Try again' })).toBeVisible()
  // Before the click: B's run may finish on its first polls.
  s.seriesHold = false
  s.seriesTitle = 'Heaven Book 2'
  await panel.getByRole('button', { name: 'Try again' }).click()
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  expect(posted(s, '/api/sources/alpha/series').map((c) => c.body)).toEqual([
    { series_id: 'a0' }, { series_id: 'a1' }, { series_id: 'a1' },
  ])
  expect(s.unmocked).toEqual([])
})

test('on load, a finished job for another series is not shown under the remembered one', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a1', title: 'Heaven Book 2' }))
  })
  const s = await mockSources(page, { series: 'done', seriesId: 'a0' })
  await page.goto('/#/sources')
  await expect.poll(() => s.calls.some((c) => c.path === '/api/sources/jobs/sources_series_alpha/result')).toBe(true)
  await page.waitForTimeout(500)
  await expect(page.getByRole('region', { name: 'Series' })).toHaveCount(0)
  await expect(page.getByText(/124 chapters/)).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('on load, a running job for another series shows as busy, not as loading the remembered one', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a1', title: 'Heaven Book 2' }))
  })
  const s = await mockSources(page, { series: 'running', seriesId: 'a0', seriesHold: true })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Another series from Alpha Comics is still loading.')).toBeVisible()
  await expect(panel.getByText(/Loading the series/)).toHaveCount(0)
  await panel.getByRole('button', { name: 'Cancel it' }).click()
  await expect.poll(() => posted(s, '/api/jobs/sources_series_alpha/cancel').length).toBe(1)
  // Once the other run has ended the panel stays, and Try again loads the remembered series.
  await expect(panel.getByText('The other series stopped loading.')).toBeVisible()
  await expect(panel.getByText(/Another series/)).toHaveCount(0)
  await panel.getByRole('button', { name: 'Try again' }).click()
  s.seriesHold = false
  s.seriesTitle = 'Heaven Book 2'
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  expect(posted(s, '/api/sources/alpha/series').map((c) => c.body)).toEqual([{ series_id: 'a1' }])
  expect(s.unmocked).toEqual([])
})

test('on load, a running job for the remembered series shows its loading line', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a1', title: 'Heaven Book 2' }))
  })
  const s = await mockSources(page, { series: 'running', seriesId: 'a1', seriesHold: true })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText(/Loading the series/)).toBeVisible()
  await expect(panel.getByText(/Another series/)).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('source settings: health text, On rollback, save only changes, 422, clear cache', async ({ page }) => {
  const s = await mockSources(page)
  let enabledStatus = 500
  await page.route(/\/api\/sources\/alpha\/enabled$/, (route) => {
    s.calls.push({ method: 'POST', path: '/api/sources/alpha/enabled', body: route.request().postDataJSON() })
    return route.fulfill({ status: enabledStatus, contentType: 'application/json', body: JSON.stringify({ error: { code: 'internal_error', message: 'boom' } }) })
  })
  let saveStatus = 422
  await page.route(/\/api\/sources\/settings$/, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(SETTINGS) })
    }
    s.calls.push({ method: 'POST', path: '/api/sources/settings', body: route.request().postDataJSON() })
    if (saveStatus === 422) {
      return route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: { code: 'invalid_input', message: "pace_min_delay can't be below 3 seconds." } }) })
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...SETTINGS, pace_max_delay: 10 }) })
  })
  await page.route(/\/api\/sources\/cache\/clear$/, (route) => {
    s.calls.push({ method: 'POST', path: '/api/sources/cache/clear', body: route.request().postDataJSON() })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ entries: 0, bytes: 0 }) })
  })

  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await expect(settings.getByText('3 of 3 sources on · 3–8 s gap · cache: Keep originals')).toBeVisible()
  await settings.getByText('Source settings').click()
  const table = settings.locator('table.sources-table')
  await expect(table.getByText('OK')).toBeVisible()
  await expect(table.getByText('Failing')).toBeVisible()
  await expect(table.getByText('Paused')).toBeVisible()

  // On: optimistic, rolled back on a 500.
  const on = settings.getByRole('switch', { name: 'On: Alpha Comics' })
  await on.click()
  await expect(settings.getByRole('alert')).toBeVisible()
  await expect(on).toBeChecked()
  expect(posted(s, '/api/sources/alpha/enabled')[0].body).toEqual({ enabled: false })
  enabledStatus = 200

  // Pacing & cache: shown in the settings body, no second fold.
  await expect(settings.getByRole('group', { name: 'Pacing & cache' })).toBeVisible()
  await expect(settings.getByRole('switch', { name: 'Auto-import new chapters' })).not.toBeChecked()
  await expect(settings.getByRole('checkbox')).toHaveCount(0)
  const save = settings.getByRole('button', { name: 'Save settings' })
  await expect(save).toBeDisabled()
  await expect(settings.getByText('No changes to save.')).toBeVisible()
  await settings.getByLabel('Gap max (s)').fill('2')
  await expect(settings.getByText('Must be at least the min.')).toBeVisible()
  await expect(save).toBeDisabled()
  await settings.getByLabel('Gap max (s)').fill('10')
  await expect(save).toBeEnabled()
  await expect(save).toHaveClass(/primary/)
  await save.click()
  await expect(settings.getByText("pace_min_delay can't be below 3 seconds.")).toBeVisible()
  expect(posted(s, '/api/sources/settings')[0].body).toEqual({ pace_max_delay: 10 })
  saveStatus = 200
  await save.click()
  await expect(settings.getByText('Saved.')).toBeVisible()
  // Cache size limit: a whole number of MB, 0 = no limit.
  const limit = settings.getByRole('spinbutton', { name: 'Cache limit (MB)' })
  await expect(limit).toHaveValue('0')
  await limit.fill('500')
  await save.click()
  await expect.poll(() => posted(s, '/api/sources/settings').at(-1)?.body).toEqual({ cache_max_mb: 500 })

  // Clear cache: two presses, then the line updates.
  await expect(settings.getByText('Cache: 120 items · 45.2 MB')).toBeVisible()
  await settings.getByRole('button', { name: 'Clear cache raw-content cache' }).click()
  expect(posted(s, '/api/sources/cache/clear')).toHaveLength(0)
  await settings.getByRole('button', { name: 'Confirm clear raw-content cache' }).click()
  await expect(settings.getByText('Cache: empty')).toBeVisible()
  expect(posted(s, '/api/sources/cache/clear')[0].body).toEqual({ confirm: true })

  // At most one filled primary per card or region.
  for (const panel of await page.locator('.sources-page > section').all()) {
    expect(await panel.locator('.btn-primary:visible:not(:disabled)').count()).toBeLessThanOrEqual(1)
  }
  expect(s.unmocked).toEqual([])
})
