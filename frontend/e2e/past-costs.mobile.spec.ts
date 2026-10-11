import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project: Past costs check -> apply -> undo, and the 409 reset, fit 390px with 44px targets.
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

async function noSideways(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

async function openCard(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  return page.getByRole('region', { name: 'Past costs' })
}

test('past costs: preview is required, then apply and undo, on a phone', async ({ page }) => {
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
  const card = await openCard(page)
  // Apply is not offered until a check has run.
  await expect(card.getByRole('button', { name: /Apply/ })).toHaveCount(0)
  const check = card.getByRole('button', { name: 'Check past costs' })
  expect(await hitHeight(check)).toBeGreaterThanOrEqual(44)
  await check.tap()
  await expect(card.getByTestId('recost-summary')).toContainText('about $8.00')
  await expect(card.getByRole('table')).toContainText('claude-sonnet-5-5')
  await noSideways(page)

  const apply = card.getByRole('button', { name: /Apply/ })
  expect(await hitHeight(apply)).toBeGreaterThanOrEqual(44)
  await apply.tap()
  const confirm = card.getByRole('button', { name: /Confirm apply/ })
  expect(await hitHeight(confirm)).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await confirm.tap()
  await expect(card.getByTestId('recost-result')).toContainText('Re-costed 2 entries. This month now shows $18.00.')

  const undo = card.getByRole('button', { name: 'Undo re-cost' })
  expect(await hitHeight(undo)).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await undo.tap()
  await expect(card.getByTestId('recost-result')).toContainText('Restored 2 entries')
  await noSideways(page)
})

test('past costs: a 409 on apply drops the stale preview on a phone', async ({ page }) => {
  let body: unknown = null
  await page.route('**/api/settings/usage-recost', (route) => route.fulfill({ json: PREVIEW }))
  await page.route('**/api/settings/usage-recost/apply', (route) => {
    body = route.request().postDataJSON()
    return route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'The past costs changed since you checked them. Check again before applying.' } },
    })
  })
  const card = await openCard(page)
  await card.getByRole('button', { name: 'Check past costs' }).tap()
  await card.getByRole('button', { name: /Apply/ }).tap()
  await card.getByRole('button', { name: /Confirm apply/ }).tap()
  await expect(card.getByText(/changed since you checked/)).toBeVisible()
  expect(body).toEqual({ confirm: true, previewed: 2, fingerprint: 'abc123' })
  await expect(card.getByRole('button', { name: /Apply/ })).toHaveCount(0)
  await expect(card.getByTestId('recost-summary')).toHaveCount(0)
  const again = card.getByRole('button', { name: 'Check past costs' })
  await expect(again).toBeEnabled()
  expect(await hitHeight(again)).toBeGreaterThanOrEqual(44)
  await noSideways(page)
})
