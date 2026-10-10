import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { mockSpendHistory } from './spendHistoryMocks'

// Phone layout: no sideways scroll, month and download targets at least 44px tall.
test('spend history fits a phone with 44px targets', async ({ page }) => {
  await mockSpendHistory(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Spend history' })
  await expect(card.getByTestId('spend-breakdown')).toBeVisible()
  const targets = [...(await card.getByRole('button').all()), card.getByRole('link', { name: 'Download CSV' })]
  for (const t of targets) {
    const box = await t.boundingBox()
    expect(box && box.height).toBeGreaterThanOrEqual(44)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/spend-history-phone.png', fullPage: true })
})
