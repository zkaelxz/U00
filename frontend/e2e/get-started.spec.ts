import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'

// Empty-library view: the Make subtitles card replaces the step list and the
// translator radios. Stats and engines are mocked; nothing real is changed.

test('shows Make subtitles with the Discover and Sources links, and dismisses for good', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  const view = page.getByRole('region', { name: 'Get started' })
  await expect(view.getByRole('region', { name: 'Make subtitles' })).toBeVisible()
  await expect(view.locator('.get-started-steps')).toHaveCount(0)
  await expect(view.getByRole('radio')).toHaveCount(0)
  await expect(view.getByRole('link', { name: 'Discover' })).toHaveAttribute('href', '#/discover')
  await expect(view.getByRole('link', { name: 'Sources' })).toHaveAttribute('href', '#/sources')

  // The saved default (Claude) has no key: the card says so.
  await expect(view.getByLabel('Translator')).toHaveValue('claude')
  await expect(view.getByTestId('preflight-key')).toBeVisible()

  await view.getByRole('button', { name: 'Dismiss' }).click()
  await expect(view).toHaveCount(0)
  // Dismissing hides the first-run wrapper only; the card stays on the Library.
  await expect(page.getByRole('region', { name: 'Make subtitles' })).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
})

test('a library with a title never shows the first-run view', async ({ page }) => {
  await mockFirstRun(page, 1)
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Make subtitles' })).toBeVisible()
})
