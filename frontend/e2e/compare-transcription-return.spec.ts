import { expect, test } from './fixtures'
import { mockCompare } from './compareMocks'
import { openFoldFor } from './reviewFolds'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Leaving Review and coming back must not lose a comparison: a finished result the
// server still holds is shown again, a run still going is picked up, and "none" is quiet.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

async function open(page: import('@playwright/test').Page) {
  await page.goto('/#/drama/3/review')
  await openFoldFor(page, 'Compare transcription')
  const box = page.getByTestId('compare-transcription')
  await expect(box).toBeVisible()
  return box
}

test('a finished result is shown again after leaving and coming back, and Clear discards it for good', async ({ page }) => {
  await mockCompare(page, { initial: 'done' })
  const box = await open(page)
  await expect(box.getByTestId('compare-last-run')).toContainText('Last comparison:')
  await expect(box.getByTestId('compare-last-run')).toContainText('3 lines, whisper · tiny')
  await expect(box.getByTestId('compare-row')).toHaveCount(2)

  await page.goto('/#/drama/3/translate')
  await page.goto('/#/drama/3/review')
  await openFoldFor(page, 'Compare transcription')
  await expect(page.getByTestId('compare-row')).toHaveCount(2)

  await page.getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByTestId('compare-results')).toHaveCount(0)
  await page.reload()
  await openFoldFor(page, 'Compare transcription')
  await expect(page.getByTestId('compare-transcription')).toBeVisible()
  await expect(page.getByTestId('compare-results')).toHaveCount(0)
})

test('coming back during a run shows its progress and Compare stays off', async ({ page }) => {
  await mockCompare(page, { initial: 'running' })
  const box = await open(page)
  await expect(box.getByTestId('compare-progress')).toContainText('Line 2 of 3')
  await expect(box.getByRole('button', { name: 'Comparing…' })).toBeDisabled()
})

test('with no stored result nothing shows and there is no error banner', async ({ page }) => {
  await mockCompare(page)
  const box = await open(page)
  await expect(box.getByTestId('compare-results')).toHaveCount(0)
  await expect(page.locator('.error-banner')).toHaveCount(0)
})

test('the "Which lines" choice is remembered', async ({ page }) => {
  await mockCompare(page)
  const box = await open(page)
  await box.getByLabel('Which lines').selectOption('range')
  await page.reload()
  await openFoldFor(page, 'Compare transcription')
  await expect(page.getByTestId('compare-transcription').getByLabel('Which lines')).toHaveValue('range')
})

// Simple leaves Compare transcription out of the tools, so there the link must bring it in (the Jobs page links here).
for (const view of ['full', 'simple'] as const) test(`the link a finished comparison gets opens Look for problems with the section open (${view} view)`, async ({ page }) => {
  await page.addInitScript((v) => localStorage.setItem('baihe.pref.title.view', JSON.stringify(v)), view)
  await mockCompare(page, { initial: 'done' })
  await page.goto('/#/drama/3/review?tools=problems&focus=compare')
  await expect(page.getByRole('tab', { name: 'Look for problems' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('compare-transcription')).toBeVisible()
  await expect(page.getByTestId('compare-row')).toHaveCount(2)
  // The link from before the tools were grouped named History; it opens the same place.
  await page.goto('about:blank')
  await page.goto('/#/drama/3/review?tools=history&focus=compare')
  await expect(page.getByRole('tab', { name: 'Look for problems' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('compare-transcription')).toBeVisible()
})
