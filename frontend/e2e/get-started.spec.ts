import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'

// Empty-library view: a card that points at the Add page and the
// ways to find a title. Stats are mocked; nothing real is changed.

test('lists the first steps with no key, and hides until shown again', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  const view = page.getByRole('region', { name: 'Get started' })
  await expect(view.getByRole('link', { name: 'Add your first title', exact: true })).toHaveAttribute('href', '#/make-subtitles')
  await expect(view.getByRole('link', { name: 'Add a translation key' })).toHaveAttribute('href', '#/settings')
  await expect(view.getByRole('link', { name: 'Install transcription' })).toHaveAttribute('href', '#/diagnostics?card=recommended')
  // The Add box is on Find & Add only, and the header has no second way in while the card shows.
  await expect(page.getByRole('region', { name: 'Add' })).toHaveCount(0)
  await expect(page.locator('.page-head').getByRole('link')).toHaveCount(0)

  await view.getByRole('button', { name: 'Hide' }).click()
  await expect(view).toHaveCount(0)
  await expect(page.locator('.page-head').getByRole('link', { name: '+ Add', exact: true })).toHaveAttribute('href', '#/make-subtitles')
  await page.reload()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Show first steps' }).click()
  await expect(page.getByRole('region', { name: 'Get started' })).toBeVisible()
})

test('the card opens the Add page', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  await page.getByRole('region', { name: 'Get started' }).getByRole('link', { name: 'Add your first title', exact: true }).click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(page.getByRole('heading', { name: 'Add', level: 1 })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Add' }).getByRole('textbox', { name: 'Paste a link' })).toBeVisible()
})

test('a library with a title shows + Add and no first-run card', async ({ page }) => {
  await mockFirstRun(page, 1)
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Add' })).toHaveCount(0)
  await expect(page.locator('.page-head').getByRole('link')).toHaveCount(1)
  await page.locator('.page-head').getByRole('link', { name: '+ Add', exact: true }).click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
})
