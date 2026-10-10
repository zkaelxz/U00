import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Settings > Past costs: check -> apply (confirm) -> undo, all against mocked routes.
const PREVIEW = {
  rows: 2,
  models: [{ model: 'claude-sonnet-5-5', rows: 2, stored_usd: 40, recomputed_usd: 8 }],
  stored_usd: 40,
  recomputed_usd: 8,
  difference_usd: 32,
  month_stored_usd: 50,
  month_recomputed_usd: 18,
  recosted_rows: 0,
  fingerprint: 'abc123',
}

test('past costs: check, apply with a confirm, undo', async ({ page }) => {
  let recosted = 0
  await page.route('**/api/settings/usage-recost', (route) => route.fulfill({ json: { ...PREVIEW, recosted_rows: recosted } }))
  await page.route('**/api/settings/usage-recost/apply', (route) => {
    recosted = 2
    return route.fulfill({ json: { rows: 2, month_spend_usd: 18, recosted_rows: 2 } })
  })
  await page.route('**/api/settings/usage-recost/undo', (route) => {
    recosted = 0
    return route.fulfill({ json: { rows: 2, month_spend_usd: 50, recosted_rows: 0 } })
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Past costs' })
  await card.getByRole('button', { name: 'Check past costs' }).click()
  await expect(card.getByTestId('recost-summary')).toContainText('about $8.00')
  await expect(card.getByRole('table')).toContainText('claude-sonnet-5-5')
  await card.getByRole('button', { name: /Apply/ }).click()
  await card.getByRole('button', { name: /Confirm apply/ }).click()
  await expect(card.getByTestId('recost-result')).toContainText('Re-costed 2 entries. This month now shows $18.00.')
  await expect(page.getByText('2 re-costed')).toBeVisible()
  await card.getByRole('button', { name: 'Undo re-cost' }).click()
  await expect(card.getByTestId('recost-result')).toContainText('Restored 2 entries')
})

test('past costs: a 409 on apply drops the stale preview and keeps only Check again', async ({ page }) => {
  let body: unknown = null
  await page.route('**/api/settings/usage-recost', (route) => route.fulfill({ json: PREVIEW }))
  await page.route('**/api/settings/usage-recost/apply', (route) => {
    body = route.request().postDataJSON()
    return route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'The past costs changed since you checked them. Check again before applying.' } },
    })
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Past costs' })
  await card.getByRole('button', { name: 'Check past costs' }).click()
  await card.getByRole('button', { name: /Apply/ }).click()
  await card.getByRole('button', { name: /Confirm apply/ }).click()
  await expect(card.getByText(/changed since you checked/)).toBeVisible()
  expect(body).toEqual({ confirm: true, previewed: 2, fingerprint: 'abc123' })
  await expect(card.getByRole('button', { name: /Apply/ })).toHaveCount(0)
  await expect(card.getByTestId('recost-summary')).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Check past costs' })).toBeEnabled()
})
