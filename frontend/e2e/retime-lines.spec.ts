import { expect, test } from '@playwright/test'

import { compareAction, retimeAction, seedLines, tickBox } from './compareSelectedHelpers'
import { mockRetime } from './retimeMocks'

// Review: tick lines, press "Re-time with Qwen3 aligner…"; the section opens,
// Run sends exactly those ids, proposals show before/after times, and "Use this"
// sends the proposed times back for the compare-and-set apply.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('tick 3 lines, re-time them, review the proposals and use one', async ({ page }) => {
  const ids = seedLines(50)
  const seen = await mockRetime(page, { hold: true })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [2, 5, 9]) await tickBox(page, n).check()
  await expect(compareAction(page)).toBeEnabled()
  await expect(retimeAction(page)).toBeEnabled()
  await retimeAction(page).click()

  const panel = page.getByTestId('retime-lines')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('retime-count')).toHaveText('3 lines ticked')
  await panel.getByRole('button', { name: 'Re-time ticked lines' }).click()
  await expect(panel.getByTestId('retime-progress')).toBeVisible()
  expect(seen.runs).toEqual([{ line_ids: [ids[1], ids[4], ids[8]] }])
  seen.release()

  const rows = panel.getByTestId('retime-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toContainText('0:02.00 – 0:03.50')
  await expect(rows.first()).toContainText('0:01.60 – 0:03.40 (−0.40 s)')
  await expect(rows.nth(1)).toContainText('check this one')
  await expect(panel.getByTestId('retime-device-notice')).toContainText('ran on the CPU')
  await expect(panel).toContainText('no text to align')

  await rows.first().getByRole('button', { name: 'Use this' }).click()
  await expect(panel.getByTestId('retime-note')).toContainText('Re-timed 1 line')
  expect(seen.applies[0]).toEqual({ job_id: 'retime_3', items: [{ line_id: 11, expected_new_start: 1.6, expected_new_end: 3.4 }] })
  await expect(rows).toHaveCount(1)
  await panel.getByRole('button', { name: 'Use all shown' }).click()
  await expect.poll(() => seen.applies[1]).toEqual({ job_id: 'retime_3', items: [{ line_id: 12, expected_new_start: 7.75, expected_new_end: 9.5 }] })
})

test('more than 200 ticked: the action is off and says why', async ({ page }) => {
  seedLines(250)
  await mockRetime(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (let p = 0; p < 6; p++) {
    await page.getByRole('button', { name: 'Select all shown' }).click()
    if (p < 5) await page.getByRole('button', { name: 'Next page' }).first().click()
  }
  await expect(retimeAction(page)).toBeDisabled()
  await expect(page.getByTestId('selection-bar')).toContainText('240 lines ticked; re-timing takes up to 200 at a time')
})
