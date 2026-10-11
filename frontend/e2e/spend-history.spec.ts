import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { mockSpendHistory } from './spendHistoryMocks'

// Settings > Spend history against mocked routes: month table, then a month's breakdowns.
test('spend history: months, a month tap, breakdowns and the estimate note', async ({ page }) => {
  await mockSpendHistory(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Spend history' })
  await expect(card).toContainText('Estimates, priced from the app')
  const months = card.getByRole('table', { name: 'Spend by month' })
  await expect(months).toContainText('Oct 2026')
  await expect(months).toContainText('$12.50')
  await expect(months).toContainText('Cap counts $7.25 since reset')
  await expect(card.getByRole('table', { name: 'By operation' })).toContainText('Benchmark judge')
  await expect(card.getByRole('table', { name: 'By title' })).toContainText('deleted title')
  await card.getByRole('button', { name: 'Sep 2026' }).click()
  await expect(card.getByRole('table', { name: 'By operation' })).toContainText('Qa')
  await expect(card.getByRole('table', { name: 'By engine and model' })).toContainText('openai / gpt-x')
  await expect(card.getByRole('link', { name: 'Download CSV' })).toHaveAttribute('href', '/api/settings/spend-history/export.csv')
})

test('spend history: no calls yet', async ({ page }) => {
  await mockSpendHistory(page, { months: [], selected_month: null, cap_reset_at: null, max_months: 36, by_operation: [], by_engine_model: [], by_title: [] })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  await expect(page.getByRole('region', { name: 'Spend history' }).getByTestId('spend-empty')).toBeVisible()
})
