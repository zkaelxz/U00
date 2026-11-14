import { expect, test } from '@playwright/test'

import { clearTurns, guesses, row, seedBlankSpeakers, speakers } from './speakerGuessHelpers'

// Review: a line with no speaker offers the speakers around it and the voice detection's
// guess as buttons; N steps between such lines and 1/2/3 take a guess and move on.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  clearTurns()
})

for (const view of ['full', 'simple'] as const) test(`fill blank speakers from the suggestions (${view} view)`, async ({ page }) => {
  seedBlankSpeakers()
  await page.addInitScript((v) => localStorage.setItem('baihe.pref.title.view', JSON.stringify(v)), view)
  await page.goto('/#/drama/3/review')
  // Simple opens on the lines that need a look; blank speakers are under All.
  if (view === 'simple') await page.getByRole('radio', { name: /^All/ }).check()
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(7)

  // The best-supported speaker comes first; named lines get no buttons.
  await expect(guesses(page, 2).getByRole('button')).toHaveText([/Cray/, /Anok/])
  await expect(guesses(page, 2).getByRole('button', { name: 'Cray: Same as the line below and the voice detection’s unsure guess' })).toBeVisible()
  await expect(guesses(page, 1)).toHaveCount(0)
  await expect(guesses(page, 6).getByRole('button')).toHaveText([/Anok/, /Cray/])

  // N goes to the first line with no speaker; 1 takes its first guess and moves to the next one.
  await page.getByRole('button', { name: 'Next with no speaker (3) ›' }).click()
  await expect(row(page, 2)).toBeFocused()
  await page.keyboard.press('1')
  await expect(row(page, 2).locator('.review-speaker')).toHaveText('Cray')
  await expect(guesses(page, 2)).toHaveCount(0)
  await expect(page.getByTestId('speaker-undo')).toContainText('Line #2 is now Cray.')
  await expect(row(page, 3)).toBeFocused()

  // #3 now has Cray above and below; 2 takes the voice guess instead.
  await expect(guesses(page, 3).getByRole('button')).toHaveText([/Cray/, /Anok/])
  await page.keyboard.press('2')
  await expect(row(page, 3).locator('.review-speaker')).toHaveText('Anok')
  await expect(row(page, 6)).toBeFocused()

  // A click sets the speaker too, and stays on the line.
  await guesses(page, 6).getByRole('button', { name: /^Cray/ }).click()
  await expect(row(page, 6).locator('.review-speaker')).toHaveText('Cray')
  await expect(page.getByRole('button', { name: /Next with no speaker/ })).toHaveCount(0)
  expect(speakers()).toEqual(['Anok', 'Cray', 'Anok', 'Cray', 'Anok', 'Cray', 'Cray'])

  // Undo puts the last one back to no speaker, with its buttons.
  await page.getByTestId('speaker-undo').getByRole('button', { name: 'Undo' }).click()
  await expect(guesses(page, 6).getByRole('button')).toHaveText([/Anok/, /Cray/])
  await expect(page.getByRole('button', { name: 'Next with no speaker (1) ›' })).toBeVisible()
  expect(speakers()[5] || null).toBeNull()

  // N from the top finds it again.
  await row(page, 1).click()
  await page.keyboard.press('n')
  await expect(row(page, 6)).toBeFocused()
})
