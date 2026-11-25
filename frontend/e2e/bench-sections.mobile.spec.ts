import { expect, test } from '@playwright/test'

import { DEFAULT_OPEN, SECTIONS, checkHelp, expectExpanded, openBench, selectTab, toggle } from './benchSectionsCases'
import { hitHeight, installHitArea } from './hitArea'
import { expectPickedTab } from './tabPicker'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

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
    await selectTab(page, title)
    expect(await hitHeight(page.locator('.ui-tab-picker-btn')), `${title} picker`).toBeGreaterThanOrEqual(44)
    // Quality is a plain Section (a details summary), not a bench section with a toggle button.
    const heading = title === 'Quality' ? page.locator('summary').filter({ hasText: 'Quality' }) : toggle(page, title)
    expect(await hitHeight(heading), title).toBeGreaterThanOrEqual(44)
    expect(await noSideways(page)).toBeLessThanOrEqual(0)
  }
})

test('a tap folds a section, the choice survives a reload and the help opens on a tap', async ({ page }) => {
  await openBench(page)
  await toggle(page, 'Golden sets').tap()
  await selectTab(page, 'Run a benchmark')
  await expectExpanded(page, { 'Golden sets': false, 'Run a benchmark': true })
  await page.reload()
  await expect(page.locator('.bench-section-toggle')).toHaveCount(6)
  // The last tab is remembered too.
  await expectPickedTab(page, 'Benchmark Lab', 'Run a benchmark')
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
