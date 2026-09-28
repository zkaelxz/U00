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
