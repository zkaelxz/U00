import { expect, test } from '@playwright/test'

import { DEFAULT_OPEN, SECTIONS, benchTab, checkHelp, expectExpanded, openBench, selectTab, toggle } from './benchSectionsCases'

// Benchmark Lab page: the four sections are tabs, a section folds under its heading, the choices survive
// a reload, and the intro and the (i) help explain the steps. Read-only against the seeded e2e library.

test('intro, header and defaults: every section is open in its own tab', async ({ page }) => {
  await openBench(page)
  await expect(page.getByTestId('bench-intro')).toContainText('Your titles and lines are never touched')
  await expect(page.getByRole('link', { name: '‹ Diagnostics' })).toHaveAttribute('href', '#/diagnostics')
  await expect(page.getByText('Up to 4 engines per run')).toBeVisible()
  await expect(page.getByRole('tab')).toHaveText(SECTIONS)
  await expectExpanded(page, DEFAULT_OPEN)
  // Only the selected tab's panel shows.
  await expect(page.getByRole('tabpanel')).toHaveCount(1)
  await expect(page.getByRole('tab', { selected: true })).toHaveText('Golden sets')
})

test('a tab shows one section; the others are hidden until their tab is picked', async ({ page }) => {
  await openBench(page)
  await expect(page.getByRole('region', { name: 'Golden sets' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent runs' })).toBeHidden()
  await selectTab(page, 'Recent runs')
  await expect(page.getByRole('region', { name: 'Recent runs' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Golden sets' })).toBeHidden()
  // Arrow keys move between tabs.
  await benchTab(page, 'Recent runs').press('ArrowLeft')
  await expect(benchTab(page, 'Model re-evaluation')).toHaveAttribute('aria-selected', 'true')
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

test('folding a section by keyboard, and the choice survives a reload', async ({ page }) => {
  await openBench(page)
  await selectTab(page, 'Recent runs')
  const runs = toggle(page, 'Recent runs')
  await runs.focus()
  await page.keyboard.press('Enter')
  await expect(runs).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('region', { name: 'Recent runs' }).locator('.bench-section-body')).toBeHidden()
  await page.reload()
  await expect(page.locator('.bench-section-toggle')).toHaveCount(4)
  await expectExpanded(page, { ...DEFAULT_OPEN, 'Recent runs': false })
  await toggle(page, 'Recent runs').focus()
  await page.keyboard.press('Space')
  await expect(toggle(page, 'Recent runs')).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('region', { name: 'Recent runs' }).locator('.bench-section-body')).toBeVisible()
})

test('each heading has a purpose line and a help that lists the steps', async ({ page }) => {
  await openBench(page)
  await expect(page.getByRole('region', { name: 'Golden sets' })).toContainText('The reference translations that runs are scored against.')
  await checkHelp(page, 'Golden sets', /1\. Import a set/)
  await checkHelp(page, 'Run a benchmark', /3\. Press Estimate cost/)
  await checkHelp(page, 'Model re-evaluation', /1\. Production is the model you use now/)
  await checkHelp(page, 'Recent runs', /2\. Results shows one run/)
})
