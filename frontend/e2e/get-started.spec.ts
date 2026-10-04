import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'

// Empty-library card with the translator choice. Stats, engines and the
// settings write are mocked; nothing real is changed.

test('walks the steps, picks a translator and dismisses for good', async ({ page }) => {
  await mockFirstRun(page)
  const posts: unknown[] = []
  await page.route('**/api/settings', (r) => {
    if (r.request().method() !== 'POST') return r.fallback()
    posts.push(r.request().postDataJSON())
    return r.fulfill({ json: {} })
  })
  await page.goto('/')
  const card = page.getByRole('region', { name: 'Get started' })
  await expect(card.getByRole('listitem')).toHaveCount(5)
  await expect(card.getByRole('listitem').first()).toContainText('Add.')
  await expect(card.getByRole('link', { name: 'Discover' })).toHaveAttribute('href', '#/discover')
  await expect(card.getByRole('link', { name: 'Sources' })).toHaveAttribute('href', '#/sources')

  // Claude is the saved default but has no key: the card says so.
  await expect(card.getByRole('radio', { name: /Claude/ })).toBeChecked()
  await expect(card.getByTestId('translator-needs-key')).toContainText("Claude can't run until a key is added")
  await expect(card.getByRole('link', { name: 'Add it in Settings' })).toHaveAttribute('href', '#/settings')

  await card.getByRole('radio', { name: /Ollama/ }).check()
  await expect(card.getByTestId('translator-needs-key')).toHaveCount(0)
  await card.getByRole('button', { name: 'Use Ollama (local) for new dramas' }).click()
  await expect(card.getByRole('button', { name: 'Saved' })).toBeDisabled()
  expect(posts).toEqual([{ default_engine: 'ollama' }])

  await card.getByRole('button', { name: 'Dismiss' }).click()
  await expect(card).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
})

test('a library with a drama never shows it', async ({ page }) => {
  await mockFirstRun(page, 1)
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Get started' })).toHaveCount(0)
})

test('a server error on saving shows as plain text, never raw', async ({ page }) => {
  await mockFirstRun(page)
  await page.route('**/api/settings', (r) => r.request().method() === 'POST'
    ? r.fulfill({ status: 400, json: { error: { code: 'validation_error', message: 'That translator is not available.' } } })
    : r.fallback())
  await page.goto('/')
  const card = page.getByRole('region', { name: 'Get started' })
  await card.getByRole('radio', { name: /Ollama/ }).check()
  await card.getByRole('button', { name: /^Use Ollama/ }).click()
  await expect(card.getByRole('alert')).toContainText('Some of the values entered are not valid')
})
