import { expect, test } from '@playwright/test'

import { SECTIONS, benchTab, checkHelp, openBench, selectTab } from './benchSectionsCases'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// Benchmark Lab page: the sections are listed down the left and one shows at a time, each as a plain heading
// with no fold; the choice survives a reload, and the page (i) and each heading's (i) explain the steps.
// Read-only against the seeded e2e library.

test('header and defaults: the side list, the page help with the intro, one muted line', async ({ page }) => {
  await openBench(page)
  const pageHelp = page.getByRole('button', { name: 'Help: Benchmark Lab' })
  await pageHelp.click()
  await expect(page.getByRole('tooltip').filter({ hasText: 'Your titles and lines are never touched' })).toContainText('1. Build a set')
  await pageHelp.press('Escape')
  await expect(page.getByRole('link', { name: 'Back to Diagnostics' })).toHaveAttribute('href', '#/diagnostics')
  await expect(page.getByTestId('bench-meta')).toContainText('Up to 4 engines per run')
  await expect(page.getByRole('button', { name: 'Start blind comparison' })).toBeVisible()
  await expect(page.getByTestId('bench-view-only')).toHaveCount(0)
  const list = page.getByRole('tablist', { name: 'Benchmark Lab' })
  await expect(list).toHaveAttribute('aria-orientation', 'vertical')
  await expect(list.getByRole('tab')).toHaveText(SECTIONS)
  // Only the selected section's pane shows.
  await expect(page.getByRole('tabpanel')).toHaveCount(1)
  await expect(page.getByRole('tab', { selected: true })).toHaveText('Golden sets')
})

test('Add cases and Golden sets fold: the main part is open, the rest closed, and a choice is remembered', async ({ page }) => {
  await openBench(page)
  await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('baihe.section.benchmark.')).forEach((k) => localStorage.removeItem(k)))
  await page.reload()
  await expect(page.locator('.bench-page .card')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Golden sets', level: 2 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Build a set from a reviewed title' })).toBeVisible()
  await selectTab(page, 'Add cases')
  const add = page.getByRole('tabpanel')
  for (const name of ['Your own clips', "Add a title's corrections to a test set", 'Import golden set', 'Add one case']) {
    await expect(add.getByRole('heading', { name })).toBeVisible()
  }
  const fold = (name: string) => add.locator('details.section', { has: page.getByRole('heading', { name, exact: true }) })
  await expect(fold('Your own clips')).toHaveAttribute('open', '')
  await expect(fold('Add one case')).not.toHaveAttribute('open', '')
  await fold('Add one case').locator('summary').click()
  await expect(fold('Add one case')).toHaveAttribute('open', '')
  await page.reload()
  await expect(fold('Add one case')).toHaveAttribute('open', '')
  await expect(add.locator('details.section > summary', { hasText: 'Hard-sub reading result' })).toBeVisible()
})

test('a tab shows one section; the others are hidden until their tab is picked', async ({ page }) => {
  await openBench(page)
  await expect(page.getByRole('region', { name: 'Golden sets' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent runs' })).toBeHidden()
  await selectTab(page, 'Recent runs')
  await expect(page.getByRole('region', { name: 'Recent runs' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Golden sets' })).toBeHidden()
  // Up and Down move along the list.
  await benchTab(page, 'Recent runs').press('ArrowUp')
  await expect(benchTab(page, 'Model re-evaluation')).toHaveAttribute('aria-selected', 'true')
  await benchTab(page, 'Model re-evaluation').press('ArrowDown')
  await expect(benchTab(page, 'Recent runs')).toHaveAttribute('aria-selected', 'true')
})

test('the last tab is remembered and a deep link wins over it', async ({ page }) => {
  await openBench(page)
  await selectTab(page, 'Model re-evaluation')
  await page.reload()
  await expect(benchTab(page, 'Model re-evaluation')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Model re-evaluation' })).toBeVisible()

  await page.goto('/#/benchmark?tab=runs')
  await page.reload()
  await expect(benchTab(page, 'Recent runs')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Recent runs' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Model re-evaluation' })).toBeHidden()
})

test('deep links open the sweeps and Add cases in their own panes', async ({ page }) => {
  for (const [tab, name] of [['sweep', 'Transcription sweep'], ['tsweep', 'Translation sweep'], ['add', 'Add cases']] as const) {
    await page.goto(`/#/benchmark?tab=${tab}`)
    await expect(benchTab(page, name)).toHaveAttribute('aria-selected', 'true')
  }
  await expect(page.getByRole('tabpanel').getByRole('heading', { name: 'Your own clips' })).toBeVisible()
})

test('each heading has a purpose line and a help that lists the steps', async ({ page }) => {
  await openBench(page)
  await expect(page.getByRole('region', { name: 'Golden sets' })).toContainText('The reference translations that runs are scored against.')
  await checkHelp(page, 'Golden sets', /1\. Import a set/)
  await checkHelp(page, 'Run a benchmark', /3\. Press Estimate cost/)
  await checkHelp(page, 'Model re-evaluation', /1\. Production is the model later re-evaluations compare against/)
  await checkHelp(page, 'Recent runs', /2\. Results shows one run/)
  await checkHelp(page, 'Transcription sweep', /4\. Stop any time/)
  await checkHelp(page, 'Translation sweep', /5\. Stop any time/)
})

test('away from the main PC: a View only line, and no Add cases pane', async ({ page }) => {
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.goto('/#/benchmark?tab=add')
  await expect(page.getByTestId('bench-view-only')).toHaveText('View only: runs start on the main PC')
  await expect(benchTab(page, 'Golden sets')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: 'Add cases' })).toHaveCount(0)
})
