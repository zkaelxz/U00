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

// Simple leaves Compare transcription and Re-time out of the Review tools; the selection bar's buttons for them
// must still open them, ticked far down the lines, where the person is looking.
for (const [label, testId] of [['Compare transcription…', 'compare-transcription'], ['Re-time with Qwen3 aligner…', 'retime-lines']] as const) {
  test(`Simple view: "${label}" opens its section in view`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
    seedLines(50)
    await mockCompare(page)
    // Simple opens on the lines that need a look; focus=all shows every line, as the All switch does.
    await page.goto('/#/drama/3/review?focus=all')
    await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
    for (const n of [30, 31]) {
      await tickBox(page, n).scrollIntoViewIfNeeded()
      await tickBox(page, n).check()
    }
    await page.getByTestId('selection-bar').getByRole('button', { name: label }).click()
    await expect(page.getByTestId(testId)).toBeInViewport()
  })
}
