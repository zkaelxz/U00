import { expect, test, type Page } from '@playwright/test'

import { clearDrama3Lines } from './reviewResultsSeed'
import { clearTurns, flagLine, guesses, row, seedBlankSpeakers, speakers } from './speakerGuessHelpers'

// Review: a line with no speaker offers the speakers around it and the voice detection's
// guess as buttons; N steps between such lines and 1/2/3 take a guess and move on.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  clearTurns()
  clearDrama3Lines()
})

for (const view of ['full', 'simple'] as const) test(`fill blank speakers from the suggestions (${view} view)`, async ({ page }) => {
  seedBlankSpeakers()
  await page.addInitScript((v) => localStorage.setItem('baihe.pref.title.view', JSON.stringify(v)), view)
  await page.goto('/#/drama/3/review')
  // Simple opens on the lines that need a look, which lists the blank speakers too; this flow reads them under All.
  if (view === 'simple') await page.getByRole('radio', { name: /^All/ }).check()
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(7)

  // The best-supported speaker comes first; named lines get no buttons.
  await expect(guesses(page, 2).getByRole('button')).toHaveText([/Cray/, /Anok/])
  await expect(guesses(page, 2).getByRole('button', { name: 'Cray: Same as the line below and what speaker detection heard' })).toBeVisible()
  await expect(guesses(page, 1)).toHaveCount(0)
  await expect(guesses(page, 6).getByRole('button')).toHaveText([/Anok/, /Cray/])

  // N goes to the first line with no speaker after the open one; 1 takes its first guess and moves to the next one.
  await row(page, 1).click()
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

// A queue card has no tick box, so the lines are found by their source text (line n holds 第n-1句).
const card = (page: Page, n: number) => page.locator('.review-line').filter({ hasText: `第${n - 1}句` })

test('a line with no speaker is in Simple\'s Need a look, and taking a speaker moves to the next card', async ({ page }) => {
  seedBlankSpeakers()
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', JSON.stringify('simple')))
  await page.goto('/#/drama/3/review')
  // The three blank lines are all that is listed: nothing else in this title is flagged or unsure.
  await expect(page.getByText('Need a look 3', { exact: true })).toBeVisible()
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
  // The open card is the first one; a closed card gives its reason in a few words.
  await expect(card(page, 3).getByTestId('queue-reason')).toHaveText('No speaker yet')
  await expect(guesses(page, 2).getByRole('button')).toHaveText([/Cray/, /Anok/])

  // 1 takes the first guess; the line leaves the queue and the next card opens, not the first.
  await card(page, 2).click()
  await page.keyboard.press('1')
  await expect(page.getByText('Need a look 2', { exact: true })).toBeVisible()
  await expect(card(page, 2)).toHaveCount(0)
  await expect(card(page, 3)).toBeFocused()
  expect(speakers()[1]).toBe('Cray')

  // Undo of the speaker puts the line back in the queue.
  await page.getByTestId('speaker-undo').getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Need a look 3', { exact: true })).toBeVisible()
  await expect(card(page, 2)).toBeVisible()
})

test('a flagged card with no speaker stays open after its speaker is picked, and says what still lists it', async ({ page }) => {
  seedBlankSpeakers()
  flagLine(3, 'reading_speed', '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.')
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', JSON.stringify('simple')))
  await page.goto('/#/drama/3/review')
  // The flagged line leads the list; the two others are listed for their blank speaker alone.
  await expect(page.getByText('Need a look 3', { exact: true })).toBeVisible()
  await expect(card(page, 3).locator('.review-speaker')).toHaveCount(0)

  await guesses(page, 3).getByRole('button', { name: /^Anok/ }).click()
  await expect(card(page, 3).locator('.review-speaker')).toHaveText('Anok')
  await expect(card(page, 3).getByTestId('speaker-note')).toHaveText('Speaker set to Anok. This line is still listed: fast to read, 25 characters a second.')
  await expect(page.getByText('Need a look 3', { exact: true })).toBeVisible()

  // The note goes with the Undo, and the buttons come back.
  await page.getByTestId('speaker-undo').getByRole('button', { name: 'Undo' }).click()
  await expect(card(page, 3).getByTestId('speaker-note')).toHaveCount(0)
  await expect(guesses(page, 3).getByRole('button')).toHaveCount(2)
})
