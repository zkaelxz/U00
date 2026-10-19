import { expect, test, type Page } from '@playwright/test'

import { navLink, openMenu } from './settingsNav'
import { enableDeveloperMode } from './sourceHelpers'

// Benchmark Lab is nav-visible and openable only with Developer Mode on; off, the page is a notice with a link to Settings.

async function developerModeOff(page: Page) {
  await page.route('**/api/assistant/settings', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { developer_mode: false, engine: null, model: null, engine_choices: [] } })
      : route.fallback())
}

test('with Developer Mode off the rail has no Benchmark Lab and its page is a notice with a Settings link', async ({ page }) => {
  await developerModeOff(page)
  const benchmarkCalls: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/benchmark')) benchmarkCalls.push(r.url())
  })
  await page.goto('/#/diagnostics?tab=models')
  await openMenu(page)
  await expect(navLink(page, 'Diagnostics')).toBeVisible()
  await expect(navLink(page, 'Benchmark Lab')).toHaveCount(0)

  // The Diagnostics link and a Model status compare link stay and land on the notice.
  await page.getByRole('main').or(page.locator('.app-main')).getByRole('link', { name: 'Open Benchmark Lab' }).click()
  await expect(page).toHaveURL(/#\/benchmark$/)
  await expect(page.getByRole('heading', { name: 'Benchmark Lab' })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Developer Mode is off.' })).toBeVisible()
  await page.goto('/#/benchmark?compare=claude:claude-sonnet-4-6,claude:claude-sonnet-5')
  await expect(page.getByRole('status').filter({ hasText: 'Developer Mode is off.' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Run a benchmark' })).toHaveCount(0)

  await page.getByRole('link', { name: 'Turn on in Settings' }).click()
  await expect(page).toHaveURL(/#\/settings\?section=developer-mode/)
  expect(benchmarkCalls).toEqual([])
})

test('with Developer Mode on the rail lists Benchmark Lab and a deep link opens the page', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.goto('/#/diagnostics')
  await openMenu(page)
  await navLink(page, 'Benchmark Lab').click()
  await expect(page).toHaveURL(/#\/benchmark$/)
  await expect(page.getByText('Developer Mode is off.')).toHaveCount(0)
  await page.goto('/#/benchmark?compare=claude:claude-sonnet-4-6,claude:claude-sonnet-5')
  await expect(page.getByRole('region', { name: 'Run a benchmark' })).toBeVisible()
})
