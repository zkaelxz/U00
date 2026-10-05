import { expect, test } from '@playwright/test'

import { mockCompare } from './compareMocks'
import { compareAction, seedLines, tickBox } from './compareSelectedHelpers'

// Phone project (390x844, touch): the bar action is a 44 px target, opens the
// section on the ticked lines, and nothing scrolls sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('phone: tick 3 lines, press the action, Run sends those ids', async ({ page }) => {
  const ids = seedLines(50)
  const seen = await mockCompare(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [2, 5, 9]) await tickBox(page, n).tap()
  const b = await compareAction(page).boundingBox()
  expect(Math.round(b!.height)).toBeGreaterThanOrEqual(44)
  await compareAction(page).tap()

  const panel = page.getByTestId('compare-transcription')
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('option', { name: 'Selected lines (3)' })).toBeAttached()
  await expect(panel.getByTestId('compare-estimate')).toBeVisible()
  await panel.getByRole('button', { name: 'Compare', exact: true }).tap()
  await expect(panel.getByTestId('compare-progress')).toBeVisible()
  expect(seen.runs[0]).toMatchObject({ selection: { kind: 'line_ids', line_ids: [ids[1], ids[4], ids[8]] } })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
