import { expect, test } from '@playwright/test'

import { retimeAction, seedLines, tickBox } from './compareSelectedHelpers'
import { mockRetime } from './retimeMocks'

// Phone project (390x844, touch): the bar action is a 44 px target, the results
// stack as cards, and nothing scrolls sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('phone: tick 3 lines, re-time, proposals fit the screen', async ({ page }) => {
  const ids = seedLines(50)
  const seen = await mockRetime(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [2, 5, 9]) await tickBox(page, n).tap()
  const b = await retimeAction(page).boundingBox()
  expect(Math.round(b!.height)).toBeGreaterThanOrEqual(44)
  await retimeAction(page).tap()

  const panel = page.getByTestId('retime-lines')
  await expect(panel).toBeVisible()
  const run = panel.getByRole('button', { name: 'Re-time ticked lines' })
  expect(Math.round((await run.boundingBox())!.height)).toBeGreaterThanOrEqual(44)
  await run.tap()
  await expect(panel.getByTestId('retime-row')).toHaveCount(2)
  expect(seen.runs[0]).toEqual({ line_ids: [ids[1], ids[4], ids[8]] })
  const use = panel.getByTestId('retime-row').first().getByRole('button', { name: 'Use this' })
  expect(Math.round((await use.boundingBox())!.height)).toBeGreaterThanOrEqual(44)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
