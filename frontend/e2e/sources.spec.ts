import { expect, test, type Page } from '@playwright/test'

import { mockAccess } from './sourcesAccessMocks'
import { enableDeveloperMode, nextFrames } from './sourceHelpers'
import { SERIES_LINKS, SETTINGS, mockSources, openManage, posted, searchResult } from './sourcesMocks'

// Sources page (#/sources), desktop. Every search/series/job/settings write
// is mocked (sourcesMocks.ts); nothing here reaches a real site.

// Many rows: no ancestor of the chapter list (or the list itself) is a scroll area with hidden rows.
async function noInnerScroll(page: Page) {
  const bad = await page.locator('.sources-chapters').evaluate((list) => {
    const out: string[] = []
    for (let e: HTMLElement | null = list; e && e !== document.documentElement; e = e.parentElement) {
      const oy = getComputedStyle(e).overflowY
      if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight) out.push(e.className || e.tagName)
    }
    return out
  })
  expect(bad, 'inner vertical scroll area').toEqual([])
}

test('nav, header, empty-state and disabled reasons', async ({ page }) => {
  const s = await mockSources(page)
  await page.goto('/#/sources')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
  // One level of tabs; the sources summary lives on Manage sources, with one line leading there from Find.
  await expect(page.getByRole('tab')).toHaveText(['Find', 'New chapters', 'Manage sources'])
  await expect(page.getByRole('tab', { name: 'Find' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('sources-summary')).toHaveCount(0)
  await expect(page.getByRole('tabpanel').getByRole('link', { name: 'Manage sources' })).toBeVisible()
  await page.getByRole('tab', { name: 'Manage sources' }).click()
  await expect(page).toHaveURL(/#\/sources\?tab=manage$/)
  await expect(page.getByTestId('sources-summary')).toHaveText('3 sources on · 2 searchable · 1 paused')
  await expect(page.getByTestId('sources-summary').locator('.pill-warn')).toHaveText('1 paused')
  await page.getByRole('tab', { name: 'Find' }).click()
  await expect(page).toHaveURL(/#\/sources$/)
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
  await expect(page.getByText('Alpha Comics hides some works. Turn on Adult works in Manage sources.')).toBeVisible()
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
  const tracked = [{ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1', url: '', drama_id: null, last_checked: 1, last_check_error: null, save_cbz: false },
    { source: 'beta', series_id: 'b0', title: 'Old Book', url: '', drama_id: null, last_checked: 1, last_check_error: 'Timed out.', save_cbz: false }]
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
  await page.goto('/#/sources?tab=new')
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

  // Saving new chapters as CBZ: comic sources only.
  await page.route(/\/api\/sources\/tracked\/save-cbz$/, (route) => {
    const body = route.request().postDataJSON()
    s.calls.push({ method: 'POST', path: '/api/sources/tracked/save-cbz', body })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify([{ ...tracked[0], save_cbz: body.save_cbz }, tracked[1]]) })
  })
  const saveCbz = box.getByRole('checkbox', { name: 'Save new chapters as CBZ' })
  await expect(saveCbz).toHaveCount(1)
  await saveCbz.click()
  await expect(saveCbz).toBeChecked()
  expect(posted(s, '/api/sources/tracked/save-cbz')[0].body).toEqual({ source: 'alpha', series_id: 'a0', save_cbz: true })

  await box.getByRole('button', { name: 'Dismiss' }).click()
  await expect(box.getByText('Chapter 125 · Alpha Comics')).toHaveCount(0)

  await box.getByRole('button', { name: 'Stop tracking Old Book' }).click()
  expect(posted(s, '/api/sources/tracked')).toHaveLength(0)
  await box.getByRole('button', { name: 'Confirm stop tracking Old Book' }).click()
  await expect(box.getByText('Old Book')).toHaveCount(0)
  expect(posted(s, '/api/sources/tracked')[0].body).toEqual({ source: 'beta', series_id: 'b0', tracked: false })
  expect(s.unmocked).toEqual([])
})

test('remote: search and Manage sources are PC only, no settings request', async ({ page }) => {
  const s = await mockSources(page, { local: false })
  await page.goto('/#/sources')
  await expect(page.getByText('Searching sources is PC only for now.')).toBeVisible()
  await expect(page.getByRole('search')).toHaveCount(0)
  await page.getByRole('tab', { name: 'Manage sources' }).click()
  const manage = page.locator('.sources-manage-panel')
  await expect(manage.getByText('PC only. Run this on the main PC.')).toBeVisible()
  // Members see that one line: no inner tabs, no site list.
  await expect(manage.getByRole('tablist')).toHaveCount(0)
  await expect(manage.locator('table, ul.source-cards')).toHaveCount(0)
  // The page's own mount reads are issued in the same commit as a settings read would be.
  await expect.poll(() => s.calls.some((c) => c.path === '/api/sources/notifications')).toBe(true)
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
  // The result is answered; let React commit whatever it would render from it.
  await nextFrames(page)
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

test('source detail: last failure reads in plain words, raw type only in the tooltip', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  const settings = await openManage(page)
  await settings.locator('tbody tr', { hasText: 'Beta Novels' }).getByRole('button', { name: 'Details' }).click()
  const line = settings.getByText(/Last failure .* \(site down\)/)
  await expect(line).toBeVisible()
  await expect(line).toHaveAttribute('title', 'Error type: SERVER_ERROR')
  await expect(settings.locator('.source-detail-row')).not.toContainText('SERVER_ERROR')
  expect(s.unmocked).toEqual([])
})

test('manage sources, Sites: status text, On rollback, the Fast sentence once, Adult only in Details', async ({ page }) => {
  const s = await mockSources(page)
  // Opening Details polls the site's tier-test job; nothing has run.
  await page.route(/\/api\/sources\/jobs\/sources_tiertest_alpha\/result$/, (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify({ job_id: '', status: 'idle', progress: 0, message: '', result: null }) }))
  let enabledStatus = 500
  await page.route(/\/api\/sources\/alpha\/enabled$/, (route) => {
    s.calls.push({ method: 'POST', path: '/api/sources/alpha/enabled', body: route.request().postDataJSON() })
    return route.fulfill({ status: enabledStatus, contentType: 'application/json', body: JSON.stringify({ error: { code: 'internal_error', message: 'boom' } }) })
  })
  const manage = await openManage(page)
  await expect(page.getByTestId('manage-summary')).toHaveText('3 of 3 sources on · 3–8 s gap · cache: Keep originals')
  const table = manage.locator('table.sources-table')
  await expect(table.getByText('Working')).toBeVisible()
  await expect(table.getByText('Slow')).toBeVisible()
  await expect(table.getByText('Paused')).toBeVisible()
  // All three adapters are listed, each with a Rules select and a Details button.
  await expect(table.locator('tbody tr')).toHaveCount(3)
  await expect(table.getByRole('button', { name: /^Details: / })).toHaveCount(3)

  // The Fast sentence is said once, above the table, and not on each unvetted row.
  await expect(manage.getByText("Fast is only available for sites whose rules we've checked.")).toHaveCount(1)
  await expect(manage.getByText(/Fast is off/)).toHaveCount(0)

  // Adult works and Sign-in are behind Details; the row only says "Signed in".
  await expect(manage.getByRole('switch', { name: 'Adult works' })).toHaveCount(0)
  await expect(table.locator('tbody tr', { hasText: 'Beta Novels' })).toContainText('Signed in')
  await table.getByRole('button', { name: 'Details: Alpha Comics' }).click()
  await expect(manage.getByRole('switch', { name: 'Adult works' })).toBeVisible()
  await table.getByRole('button', { name: 'Details: Alpha Comics' }).click()

  // On: optimistic, rolled back on a 500.
  const on = manage.getByRole('switch', { name: 'On: Alpha Comics' })
  await on.click()
  await expect(manage.getByRole('alert')).toBeVisible()
  await expect(on).toBeChecked()
  expect(posted(s, '/api/sources/alpha/enabled')[0].body).toEqual({ enabled: false })
  enabledStatus = 200
  expect(s.unmocked).toEqual([])
})

test('manage sources, Timing & cache: one Save, validation, cache limit, Clear cache', async ({ page }) => {
  const s = await mockSources(page)
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

  const manage = await openManage(page)
  // One tab level through the shared Tabs: the Sources tabs, none inside Manage sources.
  await expect(page.getByRole('tab')).toHaveCount(3)
  await expect(manage.getByRole('tab')).toHaveCount(0)

  // The Pacing card is closed the first time (only All sites starts open), with one summary row; the rest is behind Advanced timing.
  await manage.locator('summary', { hasText: 'Pacing' }).click()
  await expect(manage.getByTestId('pacing-summary')).toHaveText('3–8 s gap · 1 at a time · 3 retries · breaks every 8–20')
  const gapTo = manage.getByLabel('Wait between requests, to (s)')
  await expect(gapTo).toBeHidden()
  await manage.getByText('Advanced timing').click()
  await expect(gapTo).toBeVisible()
  // Every pacing field is reachable here, in its group.
  for (const name of [
    'Wait between requests, from (s)', 'Wait between requests, to (s)', 'Pages at a time', 'Retry attempts',
    'Rest after every (requests)', 'Rest after up to (requests)', 'Rest length, from (s)', 'Rest length, to (s)',
  ]) {
    await expect(manage.getByRole('spinbutton', { name })).toBeVisible()
  }

  // Save is the only primary on the tab, and says why it is off.
  const save = manage.getByRole('button', { name: 'Save settings' })
  await expect(save).toBeDisabled()
  await expect(manage.getByText('No changes to save.')).toBeVisible()
  await gapTo.fill('2')
  await expect(manage.getByText('Must be at least the min.')).toBeVisible()
  await expect(manage.getByText('Fix the values marked in Advanced timing first.')).toBeVisible()
  await expect(save).toBeDisabled()
  await gapTo.fill('10')
  await expect(save).toBeEnabled()
  await expect(save).toHaveClass(/primary/)
  expect(await manage.locator('.btn-primary:visible').count()).toBe(1)
  await save.click()
  await expect(manage.getByText("pace_min_delay can't be below 3 seconds.")).toBeVisible()
  expect(posted(s, '/api/sources/settings')[0].body).toEqual({ pace_max_delay: 10 })
  saveStatus = 200
  await save.click()
  await expect(manage.getByText('Saved.')).toBeVisible()

  // Saved pages: mode, size limit (whole MB, 0 = no limit) and Clear cache, in a closed card.
  await manage.locator('summary', { hasText: 'Saved pages' }).click()
  const limit = manage.getByRole('spinbutton', { name: 'Saved pages limit (MB)' })
  await expect(limit).toHaveValue('0')
  await limit.fill('500')
  await save.click()
  await expect.poll(() => posted(s, '/api/sources/settings').at(-1)?.body).toEqual({ cache_max_mb: 500 })

  // Clear cache: two presses, then the line updates.
  await expect(manage.getByText('Saved pages: 120 items · 45.2 MB')).toBeVisible()
  await manage.getByRole('button', { name: 'Clear cache raw-content cache' }).click()
  expect(posted(s, '/api/sources/cache/clear')).toHaveLength(0)
  await manage.getByRole('button', { name: 'Confirm clear raw-content cache' }).click()
  await expect(manage.getByText('Saved pages: empty')).toBeVisible()
  expect(posted(s, '/api/sources/cache/clear')[0].body).toEqual({ confirm: true })

  // The demo-source and diagnostics switches belong to Developer Mode; auto-import is on the New chapters card.
  await expect(manage.getByRole('switch', { name: 'Show demo source' })).toHaveCount(0)
  await expect(manage.getByRole('switch', { name: 'Extraction diagnostics' })).toHaveCount(0)
  await expect(manage.getByRole('switch', { name: 'Auto-import new chapters' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('New chapters: auto-import and the check interval sit on the card and apply on their own', async ({ page }) => {
  const tracked = [{ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1', url: '', drama_id: null, last_checked: null, last_check_error: null }]
  const s = await mockSources(page, { tracked })
  let current = { ...SETTINGS }
  await page.route(/\/api\/sources\/settings$/, (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ contentType: 'application/json', body: JSON.stringify(current) })
    const body = route.request().postDataJSON()
    s.calls.push({ method: 'POST', path: '/api/sources/settings', body })
    current = { ...current, ...body }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(current) })
  })
  await page.goto('/#/sources?tab=new')
  const box = page.getByRole('region', { name: 'New chapters' })
  const auto = box.getByRole('switch', { name: 'Auto-import new chapters' })
  await expect(auto).not.toBeChecked()
  await auto.click()
  await expect(auto).toBeChecked()
  expect(posted(s, '/api/sources/settings')[0].body).toEqual({ auto_queue_new_chapters: true })

  const hours = box.getByRole('spinbutton', { name: 'Check followed titles every (h)' })
  await expect(hours).toHaveValue('24')
  await hours.fill('200')
  await hours.press('Enter')
  await expect(box.getByText('Must be between 0 and 168.')).toBeVisible()
  expect(posted(s, '/api/sources/settings')).toHaveLength(1)
  await hours.fill('12')
  await hours.press('Enter')
  await expect.poll(() => posted(s, '/api/sources/settings').at(-1)?.body).toEqual({ check_interval_hours: 12 })
  // Nothing about Sources settings is left on this page: the link on Find leads to the rest.
  await expect(page.getByRole('region', { name: 'Source settings' })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Find' }).click()
  await expect(page.getByRole('link', { name: 'Manage sources' })).toHaveAttribute('href', '#/sources?tab=manage')
  expect(s.unmocked).toEqual([])
})

test('manage sources, Timing & cache: Developer Mode shows the demo and diagnostics switches', async ({ page }) => {
  const s = await mockSources(page)
  await enableDeveloperMode(page)
  const manage = await openManage(page, 'timing')
  await manage.getByText('Developer options').click()
  await expect(manage.getByRole('switch', { name: 'Show demo source' })).toBeVisible()
  await expect(manage.getByRole('switch', { name: 'Extraction diagnostics' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('open series with many rows scrolls with the page, no inner scrollbar', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(12), errors: {} }, series: 'done' })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  await expect(page.getByText(/Searching…/)).toBeVisible()
  s.search = 'done'
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).first().click()
  const panel = page.getByRole('region', { name: 'Series' })
  await panel.getByRole('button', { name: 'Show all 124' }).click()
  await expect(panel.getByRole('heading', { level: 4, name: 'Extras' })).toBeVisible()
  await noInnerScroll(page)
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(await page.evaluate(() => innerHeight))
})
