import { expect, test, type Page } from '@playwright/test'
import { seedLines } from './compareSelectedHelpers'
import { ctrlS } from './reviewFolds'
import { installHitArea } from './hitArea'

// Three translated lines on drama 3, written straight into the test server's throwaway library.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  seedLines(3)
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

// Make a row active by clicking its line number (not its translation, which edits).
async function activate(page: Page, n: number) {
  const row = rows(page).nth(n)
  await row.locator('.review-idx').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  return row
}

test('Mark checked badges the line, the Unreviewed filter drops it, and an edit clears the mark', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  await page.getByRole('radio', { name: /^Unreviewed/ }).check()
  await expect(rows(page)).toHaveCount(3)
  const row = await activate(page, 0)
  await row.getByRole('button', { name: 'Mark checked' }).click()
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByRole('radio', { name: 'Unreviewed 2' })).toBeChecked()
  await page.getByRole('radio', { name: /^All lines/ }).check()
  await expect(rows(page)).toHaveCount(3)
  const first = await activate(page, 0)
  await expect(first.getByTestId('line-checked')).toHaveText('Checked')
  await expect(first.getByRole('button', { name: 'Checked' })).toHaveAttribute('aria-pressed', 'true')
  await first.getByTestId('line-en').click()
  await first.getByLabel('Translation').fill('Hello again')
  await ctrlS(page, first)
  // Ctrl+S keeps the editor open; the saved line comes back unchecked.
  await expect(first.getByRole('button', { name: 'Mark checked' })).toHaveAttribute('aria-pressed', 'false')
  await expect(first.getByTestId('line-checked')).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'Unreviewed 3' })).toBeVisible()
})

