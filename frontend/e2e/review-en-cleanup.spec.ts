import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { openFoldFor } from './reviewFolds'

// Review → Restructure → Fix common errors. The cleanup routes are mocked
// with page.route (the rules themselves are covered by pytest); drama 3 is
// the seeded throwaway drama, so its lines load from the real API.

const PREVIEW = {
  drama_id: 3, lines_scanned: 3, lines_changed: 2, truncated: false, plan_hash: 'hash-1',
  rules: [
    { rule: 'extra_space', label: 'Double or stray spaces', lines: 1 },
    { rule: 'pronoun_i', label: 'Lowercase "i"', lines: 1 },
  ],
  changes: [
    { line_id: 1, idx: 0, before: 'hello  , i go', after: 'hello, I go', rules: ['extra_space', 'pronoun_i'] },
    { line_id: 2, idx: 1, before: 'the the end', after: 'the end', rules: ['doubled_word'] },
  ],
}

async function mock(page: Page, applyStatus = 200) {
  const applied: unknown[] = []
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/review-extras/dramas/3/en-cleanup/preview', (route) => route.fulfill({ json: PREVIEW }))
  await page.route('**/api/review-extras/dramas/3/en-cleanup/apply', (route) => {
    applied.push(route.request().postDataJSON())
    return applyStatus === 200
      ? route.fulfill({ json: { applied: 2, stale: 0, history_id: 5 } })
      : route.fulfill({ status: applyStatus, json: { error: { code: 'conflict', message: 'The lines changed since the preview -- preview again.' } } })
  })
  return applied
}

async function openCleanup(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)').first()).toBeVisible()
  await openFoldFor(page, 'Fix common errors')
  const sub = page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: /^Fix common errors$/ }) })
  if ((await sub.getAttribute('open')) === null) await sub.locator(':scope > summary').click()
  return sub
}

test('preview shows counts and before/after, apply sends the plan hash', async ({ page }) => {
  const applied = await mock(page)
  const sub = await openCleanup(page)
  await sub.getByRole('button', { name: 'Preview fixes' }).click()
  const preview = page.getByTestId('en-cleanup-preview')
  await expect(preview).toContainText('2 of 3 lines would change.')
  await expect(preview).toContainText('Double or stray spaces')
  await expect(preview.locator('del').first()).toHaveText('hello  , i go')
  await expect(preview.locator('ins').first()).toHaveText('hello, I go')
  expect(applied).toEqual([])

  await preview.getByRole('button', { name: 'Fix 2 lines' }).click()
  await expect(sub.getByRole('status')).toContainText('Fixed 2 lines')
  expect(applied).toEqual([{ expected_plan_hash: 'hash-1' }])
  await expect(page.getByTestId('en-cleanup-preview')).toHaveCount(0)
})

test('a changed plan shows the server refusal and writes nothing more', async ({ page }) => {
  await mock(page, 409)
  const sub = await openCleanup(page)
  await sub.getByRole('button', { name: 'Preview fixes' }).click()
  await page.getByTestId('en-cleanup-preview').getByRole('button', { name: 'Fix 2 lines' }).click()
  await expect(sub.getByRole('alert')).toContainText('changed')
})

test('phone: no sideways scroll and 44px buttons', async ({ page }) => {
  await mock(page)
  await page.setViewportSize({ width: 390, height: 844 })
  const sub = await openCleanup(page)
  await sub.getByRole('button', { name: 'Preview fixes' }).click()
  const fix = page.getByTestId('en-cleanup-preview').getByRole('button', { name: 'Fix 2 lines' })
  await expect(fix).toBeVisible()
  expect((await fix.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect((await sub.getByRole('button', { name: 'Preview again' }).boundingBox())!.height).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
})
