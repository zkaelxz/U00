import { expect, test } from '@playwright/test'

import { SECTIONS, checkHelp, openBench, selectTab } from './benchSectionsCases'
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

// Phone project (390x844, touch): the sections are one row of 44px tabs that scrolls sideways on its own, one tap
// switches section, the choice survives a reload, the (i) help opens on a tap and the page never scrolls sideways.

const noSideways = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

test('one tap per section on a 44px tab row, and nothing scrolls sideways', async ({ page }) => {
  await openBench(page)
  await expect(page.locator('.ui-tab-picker-btn')).toHaveCount(0)
  for (const title of SECTIONS) {
    await selectTab(page, title)
    const tab = page.getByRole('tab', { name: title, exact: true })
    expect(await hitHeight(tab), `${title} tab`).toBeGreaterThanOrEqual(44)
    expect(await noSideways(page)).toBeLessThanOrEqual(0)
  }
})

test('the section is remembered over a reload and the help opens on a tap', async ({ page }) => {
  await openBench(page)
  await selectTab(page, 'Run a benchmark')
  await page.reload()
  await expectPickedTab(page, 'Benchmark Lab', 'Run a benchmark')

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
