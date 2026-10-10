import { expect, test } from '@playwright/test'

import { openReview, rows, seedLines, splitSecondLine, zhTexts } from './reviewUndo'

// Phone: the Undo notice fits 390 px, its buttons are 44 px touch targets, and it works.

test.beforeEach(() => seedLines())

test('phone: split, Undo is a 44 px target inside the screen, and restores the lines', async ({ page }) => {
  await openReview(page, 3)
  await splitSecondLine(page)
  const notice = page.getByTestId('undo-notice')
  await expect(notice).toBeVisible()
  const undo = notice.getByRole('button', { name: 'Undo' })
  const dismiss = notice.getByRole('button', { name: 'Dismiss' })
  for (const b of [undo, dismiss]) {
    const box = (await b.boundingBox())!
    expect(box.height).toBeGreaterThanOrEqual(44)
    expect(box.width).toBeGreaterThanOrEqual(44)
  }
  const nb = (await notice.boundingBox())!
  expect(nb.x).toBeGreaterThanOrEqual(0)
  expect(nb.x + nb.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await undo.tap()
  await expect(rows(page)).toHaveCount(3)
  expect(await zhTexts(page)).toEqual(['你好', '再见朋友', '谢谢'])
})
