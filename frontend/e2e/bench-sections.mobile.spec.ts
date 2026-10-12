import { expect, test } from '@playwright/test'

import { DEFAULT_OPEN, SECTIONS, benchTab, checkHelp, expectExpanded, openBench, selectTab, toggle } from './benchSectionsCases'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the section headings are 44px targets, a tap folds a section, the
// choice survives a reload, the (i) help opens on a tap and nothing scrolls sideways.

const noSideways = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

test('headings are 44px tall and nothing scrolls sideways', async ({ page }) => {
  await openBench(page)
  await expectExpanded(page, DEFAULT_OPEN)
  for (const title of SECTIONS) {
    expect(await hitHeight(benchTab(page, title)), `${title} tab`).toBeGreaterThanOrEqual(44)
    await selectTab(page, title)
    expect(await hitHeight(toggle(page, title)), title).toBeGreaterThanOrEqual(44)
    expect(await noSideways(page)).toBeLessThanOrEqual(0)
  }
  // The tab row may scroll sideways on its own; the page never does.
  const row = await page.getByRole('tablist', { name: 'Benchmark Lab' }).evaluate((e) => getComputedStyle(e).overflowX)
  expect(row).toBe('auto')
})

test('a tap folds a section, the choice survives a reload and the help opens on a tap', async ({ page }) => {
  await openBench(page)
  await toggle(page, 'Golden sets').tap()
  await benchTab(page, 'Run a benchmark').tap()
  await expect(benchTab(page, 'Run a benchmark')).toHaveAttribute('aria-selected', 'true')
  await expectExpanded(page, { 'Golden sets': false, 'Run a benchmark': true })
  await page.reload()
  await expect(page.locator('.bench-section-toggle')).toHaveCount(4)
  // The last tab is remembered too.
  await expect(benchTab(page, 'Run a benchmark')).toHaveAttribute('aria-selected', 'true')
  await expectExpanded(page, { ...DEFAULT_OPEN, 'Golden sets': false })

  const help = page.getByRole('button', { name: 'Help: Run a benchmark' })
  await help.tap()
  const tip = page.getByRole('tooltip').filter({ hasText: /1\. Pick the stage/ })
  await expect(tip).toBeVisible()
  const box = await tip.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  expect(await noSideways(page)).toBeLessThanOrEqual(0)
  await checkHelp(page, 'Recent runs', /1\. Score is how close/)
})
