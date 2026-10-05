import { expect, test } from '@playwright/test'

import { mockCompare } from './compareMocks'
import { compareAction, seedLines, tickBox } from './compareSelectedHelpers'

// Review: tick lines, press "Compare transcription…" in the selection bar; the
// section opens with "Selected lines (N)" and Estimate/Run send exactly those ids.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('tick 3 lines, open Compare transcription, estimate and run send those ids', async ({ page }) => {
  const ids = seedLines(50)
  const seen = await mockCompare(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (const n of [2, 5, 9]) await tickBox(page, n).check()
  await expect(page.getByTestId('selection-bar')).toContainText('3 selected')
  await expect(compareAction(page)).toBeEnabled()
  await compareAction(page).click()

  const panel = page.getByTestId('compare-transcription')
  await expect(panel).toBeVisible()
  await expect(panel.getByLabel('Which lines')).toHaveValue('selected')
  await expect(panel.getByRole('option', { name: 'Selected lines (3)' })).toBeAttached()
  await expect(panel).toBeInViewport()
  await expect(page.getByTestId('selection-bar')).toContainText('3 selected')

  const wanted = [ids[1], ids[4], ids[8]]
  await expect(panel.getByTestId('compare-estimate')).toBeVisible()
  expect(seen.estimates.at(-1)).toMatchObject({ selection: { kind: 'line_ids', line_ids: wanted } })

  // The other choices still work.
  await panel.getByLabel('Which lines').selectOption('range')
  await expect(panel.getByLabel('From line #')).toBeVisible()
  await panel.getByLabel('Which lines').selectOption('selected')

  await panel.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(panel.getByTestId('compare-progress')).toBeVisible()
  expect(seen.runs).toHaveLength(1)
  expect(seen.runs[0]).toMatchObject({ selection: { kind: 'line_ids', line_ids: wanted } })
})

test('more than 200 ticked: the action is off and says why', async ({ page }) => {
  seedLines(250)
  await mockCompare(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  for (let p = 0; p < 6; p++) {
    await page.getByRole('button', { name: 'Select all shown' }).click()
    if (p < 5) await page.getByRole('button', { name: 'Next page' }).first().click()
  }
  const bar = page.getByTestId('selection-bar')
  await expect(bar).toContainText('240 selected')
  await expect(compareAction(page)).toBeDisabled()
  await expect(bar).toContainText('240 lines ticked; Compare transcription takes up to 200 at a time')
})
