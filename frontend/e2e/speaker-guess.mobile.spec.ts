import { expect, test } from '@playwright/test'

import { clearDrama3Lines } from './reviewResultsSeed'
import { clearTurns, flagLine, guesses, row, seedBlankSpeakers, speakers } from './speakerGuessHelpers'

// Phone: the suggested speakers fit the row and take a tap, and the Undo sits above the phone's action bar.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  clearTurns()
  clearDrama3Lines()
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

test('a phone card in Need a look offers the speakers and moves on after a tap', async ({ page }) => {
  seedBlankSpeakers()
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', JSON.stringify('simple')))
  await page.goto('/#/drama/3/review')
  await expect(page.getByTestId('queue-position')).toHaveText('1 of 3 to look at')
  await expect(page.getByTestId('queue-reason')).toHaveText('Why: This line has no speaker yet.')
  await guesses(page, 2).getByRole('button', { name: /^Cray/ }).tap()
  await expect(page.getByTestId('queue-position')).toHaveText('1 of 2 to look at')
  await expect(guesses(page, 3)).toBeVisible()
  expect(speakers()[1]).toBe('Cray')
})

test('a flagged phone card says what is left after its speaker is tapped, without sideways scroll', async ({ page }) => {
  seedBlankSpeakers()
  flagLine(3, 'reading_speed', '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.')
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', JSON.stringify('simple')))
  await page.goto('/#/drama/3/review')
  await guesses(page, 3).getByRole('button', { name: /^Anok/ }).tap()
  await expect(page.getByTestId('speaker-note')).toHaveText('Speaker set to Anok. This line is still listed: fast to read, 25 characters a second.')
  await expect(page.getByTestId('queue-position')).toHaveText('1 of 3 to look at')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
