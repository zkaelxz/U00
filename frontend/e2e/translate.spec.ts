import { expect, test } from '@playwright/test'

// Reaches the page at `#/translate`, the route the lead wires to TranslatePage.
test('translates with the offline engine and lists it in history', async ({ page }) => {
  await page.goto('/#/translate')
  await expect(page.getByRole('region', { name: 'Translate' })).toBeVisible()

  await page.getByLabel('Engine', { exact: true }).selectOption('test_offline')
  await page.getByLabel('Text to translate').fill('你好，世界')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  await expect(page.getByTestId('translate-result')).not.toBeEmpty()
  await expect(page.getByTestId('translate-history').locator('li').first()).toContainText('你好，世界')

  await page.getByLabel('Text to translate').fill('   ')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
})

test('clears history after a second press, then shows the empty list', async ({ page }) => {
  await page.goto('/#/translate')
  await page.getByLabel('Engine', { exact: true }).selectOption('test_offline')
  await page.getByLabel('Text to translate').fill('再见')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  await expect(page.getByTestId('translate-history').locator('li').first()).toContainText('再见')

  await page.getByRole('button', { name: 'Clear history' }).click()
  await page.getByRole('button', { name: 'Confirm clear translation history' }).click()
  await expect(page.getByText('No translations yet.')).toBeVisible()
  await expect(page.getByTestId('translate-history')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Clear history' })).toHaveCount(0)
})

test('another device sees why it cannot clear history', async ({ page }) => {
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }))
  await page.route('**/api/translate/history**', (r) =>
    r.fulfill({ json: { items: [{
      source_text: '你好', translated_text: 'Hello', source_language: 'zh', target_language: 'en',
      engine: 'test_offline', created_at: '2026-09-29 12:00:00',
    }] } }))
  await page.goto('/#/translate')
  await expect(page.getByTestId('translate-history')).toContainText('你好')
  await expect(page.getByText('Clearing history is PC only.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Clear history' })).toHaveCount(0)
})
