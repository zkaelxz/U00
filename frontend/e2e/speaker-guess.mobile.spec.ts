import { expect, test } from '@playwright/test'

import { clearTurns, guesses, row, seedBlankSpeakers, speakers } from './speakerGuessHelpers'

// Phone: the suggested speakers fit the row and take a tap, and the Undo sits above the phone's action bar.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  clearTurns()
})

test('tap a suggested speaker on a phone', async ({ page }) => {
  seedBlankSpeakers()
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(7)
  const chip = guesses(page, 2).getByRole('button', { name: /^Cray/ })
  await expect(chip).toBeVisible()
  const box = await chip.boundingBox()
  expect(box!.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await chip.tap()
  await expect(row(page, 2).locator('.review-speaker')).toHaveText('Cray')
  const undo = page.getByTestId('speaker-undo')
  await expect(undo).toBeInViewport()
  const bar = page.getByRole('toolbar', { name: 'Line actions' })
  if (await bar.isVisible()) {
    const u = (await undo.boundingBox())!
    const b = (await bar.boundingBox())!
    expect(u.y + u.height).toBeLessThanOrEqual(b.y + 1)
  }
  expect(speakers()[1]).toBe('Cray')
})
