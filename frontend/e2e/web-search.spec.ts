import { expect, test } from '@playwright/test'

import { mockSources, searchResult } from './sourcesMocks'
import { WEB_RESULTS, emptySearch, mockWebSearch } from './webSearchMocks'

// Web-search fallback (roadmap item 114). Sources jobs are mocked
// (sourcesMocks.ts) and so is /api/web-search (no SearXNG).

async function searchNothing(page: import('@playwright/test').Page, s: { search: string }) {
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Nowhere Title')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  s.search = 'done'
  await expect(page.getByText('No results for "Nowhere Title".')).toBeVisible()
}

test('off: an empty search points to Settings and sends nothing to the web', async ({ page }) => {
  const s = await mockSources(page, { searchBody: emptySearch() })
  const web = await mockWebSearch(page, false)
  await searchNothing(page, s)
  await expect(page.getByTestId('web-search-off')).toContainText('SearXNG')
  await expect(page.getByRole('button', { name: 'Search the web' })).toHaveCount(0)
  expect(web.searches).toEqual([])
})

test('on: web results are labelled, and "Use this link" only fills the paste-a-link box', async ({ page }) => {
  const s = await mockSources(page, { searchBody: emptySearch() })
  const web = await mockWebSearch(page, true)
  await searchNothing(page, s)
  await page.getByRole('button', { name: 'Search the web' }).click()
  const list = page.getByRole('list', { name: 'Web results' })
  await expect(page.getByTestId('web-results')).toContainText('2 web results')
  await expect(page.getByTestId('web-results')).toContainText("not from Baihe's sources")
  expect(web.searches).toEqual([{ query: 'Nowhere Title' }])
  const first = list.getByRole('listitem').first()
  await expect(first).toContainText('Web')
  await expect(first).toContainText('wiki.example')
  await expect(first).toContainText('Episode list and cast')
  const link = first.getByRole('link', { name: WEB_RESULTS.results[0].title })
  await expect(link).toHaveAttribute('href', 'https://wiki.example/nowhere')
  await expect(link).toHaveAttribute('target', '_blank')

  await list.getByRole('listitem').nth(1).getByRole('button', { name: 'Use this link' }).click()
  await expect(page.getByRole('radio', { name: 'Paste a link' })).toBeChecked()
  const box = page.getByRole('textbox', { name: 'Paste a link' })
  await expect(box).toHaveValue('https://novels.example/book/42')
  await expect(box).toBeFocused()
  // Nothing was fetched yet: the preview starts only when the user presses Preview.
  expect(s.calls.filter((c) => c.method === 'POST' && c.path.includes('/url/'))).toEqual([])
})

test('adapter results: no web-search row', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(2), errors: {} } })
  await mockWebSearch(page, true)
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  s.search = 'done'
  await expect(page.getByText('2 results', { exact: true })).toBeVisible()
  await expect(page.getByTestId('web-search')).toHaveCount(0)
  await expect(page.getByTestId('web-search-off')).toHaveCount(0)
})

test('settings: off by default, saves the address with confirm, Test, then turn on', async ({ page }) => {
  let cfg: { enabled: boolean; base_url: string | null } = { enabled: false, base_url: null }
  const saved: unknown[] = []
  await page.route('**/api/web-search/config', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      saved.push(body)
      const { confirm: _c, ...rest } = body
      void _c
      cfg = { ...cfg, ...rest }
    }
    return route.fulfill({ json: cfg })
  })
  await page.route('**/api/web-search/test', (route) => route.fulfill({ json: { ok: true, result_count: 7 } }))
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Web search' })
  await expect(card).toContainText('Off')
  const sw = card.getByRole('switch', { name: 'Use web search' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(card.getByRole('button', { name: 'Test' })).toBeDisabled()

  await card.getByRole('textbox', { name: 'SearXNG address' }).fill('http://localhost:8888')
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  expect(saved[0]).toEqual({ base_url: 'http://localhost:8888', confirm: true })

  await card.getByRole('button', { name: 'Test' }).click()
  await expect(card.getByRole('status')).toHaveText('SearXNG answered with 7 results.')

  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  expect(saved[1]).toEqual({ enabled: true })
  await expect(card).toContainText('On')
})

test('settings: a refused address change explains the key-write gate', async ({ page }) => {
  await page.route('**/api/web-search/config', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 403, json: { error: { code: 'FORBIDDEN', message: 'Not allowed from this connection.' } } })
      : route.fulfill({ json: { enabled: false, base_url: null } }),
  )
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Web search' })
  await card.getByRole('textbox', { name: 'SearXNG address' }).fill('http://localhost:8888')
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(card.getByRole('alert')).toContainText('key writes turned on')
})

test('settings: flipping the switch keeps an address typed but not saved', async ({ page }) => {
  let cfg = { enabled: false, base_url: null as string | null }
  await page.route('**/api/web-search/config', (route) => {
    if (route.request().method() === 'POST') cfg = { ...cfg, ...route.request().postDataJSON() }
    return route.fulfill({ json: cfg })
  })
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Web search' })
  const box = card.getByRole('textbox', { name: 'SearXNG address' })
  await box.fill('http://192.168.1.20:8888')
  await card.getByRole('switch', { name: 'Use web search' }).click()
  await expect(card.getByRole('switch', { name: 'Use web search' })).toHaveAttribute('aria-checked', 'true')
  await expect(box).toHaveValue('http://192.168.1.20:8888')
  await expect(card.getByRole('button', { name: 'Test' })).toHaveText('Test')
})
