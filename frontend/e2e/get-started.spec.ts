import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'

// Empty-library view: a card that points at the Make subtitles page and the
// ways to find a title. Stats are mocked; nothing real is changed.

test('lists the first steps with no key, and hides until shown again', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  const view = page.getByRole('region', { name: 'Get started' })
  await expect(view.getByRole('link', { name: 'Make subtitles' })).toHaveAttribute('href', '#/make-subtitles')
  await expect(view.getByRole('link', { name: 'Add a translation key' })).toHaveAttribute('href', '#/settings')
  await expect(view.getByRole('link', { name: 'Install transcription' })).toHaveAttribute('href', '#/diagnostics?card=recommended')
  // The flow itself lives on its own page now.
  await expect(page.getByRole('region', { name: 'Make subtitles' })).toHaveCount(0)
  await expect(page.getByLabel('Audio or video file')).toHaveCount(0)
  // One entry on the Library while the card shows: the header button waits for the card to go.
  await expect(page.locator('.app-main').getByRole('link', { name: 'Make subtitles' })).toHaveCount(1)

  await view.getByRole('button', { name: 'Hide' }).click()
  await expect(view).toHaveCount(0)
  await expect(page.locator('.app-main').getByRole('link', { name: 'Make subtitles' })).toHaveCount(1)
  await page.reload()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Show first steps' }).click()
  await expect(page.getByRole('region', { name: 'Get started' })).toBeVisible()
})

test('the card opens the Make subtitles page', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  await page.getByRole('region', { name: 'Get started' }).getByRole('link', { name: 'Make subtitles' }).click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(page.getByRole('heading', { name: 'Make subtitles', level: 1 })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Make subtitles' }).getByLabel('Audio or video file')).toBeVisible()
})

test('a library with a title shows one small Make subtitles button and no first-run card', async ({ page }) => {
  await mockFirstRun(page, 1)
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
  const button = page.locator('.app-main').getByRole('link', { name: 'Make subtitles' })
  await expect(button).toHaveCount(1)
  await expect(button).toHaveClass(/btn-sm/)
  await expect(button).toHaveAttribute('href', '#/make-subtitles')
  await expect(page.getByRole('region', { name: 'Make subtitles' })).toHaveCount(0)
})
