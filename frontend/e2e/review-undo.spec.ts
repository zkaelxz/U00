import { expect, test } from '@playwright/test'

import { openResplit } from './resplitMocks'
import { SENTENCE, editEnglishBehindTheUi, openReview, rows, seedLines, splitSecondLine, zhTexts } from './reviewUndo'

// One-click Undo on the confirmation of a structural edit. Real lines, real API.

const BEFORE = ['你好', '再见朋友', '谢谢']

test.beforeEach(() => seedLines())

test('split, then Undo brings the lines back and removes the button', async ({ page }) => {
  await openReview(page, 3)
  await splitSecondLine(page)
  const notice = page.getByTestId('undo-notice')
  await expect(notice).toContainText('Split #2 into #2–#3.')
  await expect(notice).toContainText('Records → Line history')
  expect(await zhTexts(page)).toEqual(['你好', '再见', '朋友', '谢谢'])

  // It outlasts the old 8 s status line.
  await page.waitForTimeout(9000)
  await notice.getByRole('button', { name: 'Undo' }).click()
  await expect(rows(page)).toHaveCount(3)
  expect(await zhTexts(page)).toEqual(BEFORE)
  await expect(notice).toHaveCount(0)
  await expect(page.getByRole('status').filter({ hasText: 'Undone.' })).toBeVisible()
})

test('delete, then Undo; the next structural edit replaces the offer', async ({ page }) => {
  await openReview(page, 3)
  await rows(page).nth(2).getByRole('button', { name: 'More actions for line 3' }).click()
  const sheet = page.getByRole('dialog', { name: 'Line #3' })
  await sheet.getByRole('button', { name: 'Delete line…' }).click()
  await sheet.getByRole('button', { name: /^Confirm delete #/ }).click()
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByTestId('undo-notice')).toContainText('Deleted #3.')
  await page.getByTestId('undo-notice').getByRole('button', { name: 'Undo' }).click()
  await expect(rows(page)).toHaveCount(3)
  expect(await zhTexts(page)).toEqual(BEFORE)
})

test('Undo is refused, and says why, when the lines were edited elsewhere in the meantime', async ({ page }) => {
  await openReview(page, 3)
  await splitSecondLine(page)
  editEnglishBehindTheUi('谢谢', 'Edited on another device')
  await page.getByTestId('undo-notice').getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'The lines changed since.' })).toContainText('Records → Line history')
  await expect(page.getByTestId('undo-notice')).toHaveCount(0)
  expect(await zhTexts(page)).toEqual(['你好', '再见', '朋友', '谢谢'])
})

test('saving an edit to a line retires the Undo; a reload has none', async ({ page }) => {
  await openReview(page, 3)
  await splitSecondLine(page)
  const row = rows(page).nth(3)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Thank you')
  await row.getByLabel('Translation').press('Enter')
  await expect(page.getByTestId('undo-notice')).toHaveCount(0)

  await page.getByRole('button', { name: 'More actions for line 1' }).click()
  await page.getByRole('dialog', { name: 'Line #1' }).getByRole('button', { name: 'Split line…' }).click()
  await page.getByRole('dialog', { name: 'Split line #1' }).getByRole('button', { name: 'Split line' }).click()
  await expect(page.getByTestId('undo-notice')).toBeVisible()
  await page.reload()
  await expect(rows(page)).toHaveCount(5)
  await expect(page.getByTestId('undo-notice')).toHaveCount(0)
})

test('re-split, then Undo restores the long line', async ({ page }) => {
  seedLines(true)
  const group = await openResplit(page)
  await group.getByRole('button', { name: 'Re-split long lines' }).click()
  const notice = group.getByTestId('resplit-summary')
  await expect(notice).toContainText('Split 1 line into 3')
  await notice.getByRole('button', { name: 'Undo' }).click()
  await expect(group.getByTestId('resplit-summary')).toContainText('Undone.')
  await expect(rows(page).filter({ hasText: SENTENCE.repeat(3) })).toHaveCount(1)
  await expect(rows(page)).toHaveCount(4)
})
