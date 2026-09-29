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

test('shows source and result side by side on desktop, with humanized history', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/translate')
  await page.getByLabel('Text to translate').fill('早上好')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const result = page.getByRole('region', { name: 'Result' })
  await expect(result.getByTestId('translate-result')).not.toBeEmpty()
  await expect(result.getByRole('button', { name: 'Copy' })).toBeVisible()
  await expect(result.getByRole('button', { name: /Download result \(translation\.en\.txt\)/ })).toBeVisible()
  const src = await page.getByLabel('Text to translate').boundingBox()
  const res = await result.boundingBox()
  expect(src && res && res.x > src.x + src.width - 1).toBe(true)
  expect(src && res && Math.abs(res.y - src.y) < 80).toBe(true)
  const row = page.getByTestId('translate-history').locator('li').first()
  await expect(row).toContainText('Chinese → English · Offline test')
  await expect(row).not.toContainText('test_offline')
})

test('swap flips the languages in one press and is remembered', async ({ page }) => {
  await page.goto('/#/translate')
  await expect(page.getByLabel('Source language')).toHaveValue('zh')
  await page.getByRole('button', { name: 'Swap languages' }).click()
  await expect(page.getByLabel('Source language')).toHaveCount(0)
  await page.getByLabel('Target language').selectOption('ko')
  await expect(page.getByRole('region', { name: 'Result' }).getByRole('heading')).toHaveText('Korean')
  await page.reload()
  await expect(page.getByLabel('Target language')).toHaveValue('ko')
  await page.getByRole('button', { name: 'Swap languages' }).click()
  await expect(page.getByLabel('Source language')).toHaveValue('ko')
})

test('an engine with no key is marked and says where to add one', async ({ page }) => {
  await page.goto('/#/translate')
  const engine = page.getByLabel('Engine', { exact: true })
  await expect(engine.locator('option', { hasText: 'Claude (no key)' })).toHaveCount(1)
  await expect(page.getByTestId('engine-note')).toContainText('Offline test')
  await engine.selectOption('claude')
  await expect(page.getByRole('button', { name: 'Translate', exact: true })).toBeDisabled()
  await expect(page.getByText('Still needed: an API key for Claude.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Add it in Settings' })).toHaveAttribute('href', '#/settings')
  await expect(page.getByLabel('Model')).toBeVisible()
})

test('Clear empties the text and the result', async ({ page }) => {
  await page.goto('/#/translate')
  const clear = page.getByRole('button', { name: 'Clear', exact: true })
  await expect(clear).toBeDisabled()
  await page.getByLabel('Text to translate').fill('谢谢')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  await expect(page.getByTestId('translate-result')).not.toBeEmpty()
  await clear.click()
  await expect(page.getByLabel('Text to translate')).toHaveValue('')
  await expect(page.getByTestId('translate-result')).toHaveCount(0)
  await expect(page.getByText('The translation appears here.')).toBeVisible()
})

test('history shows the last 5 until Show all', async ({ page }) => {
  const items = Array.from({ length: 7 }, (_, i) => ({
    source_text: `源 ${i}`, translated_text: `Out ${i}`, source_language: 'ja', target_language: 'en',
    engine: 'deepseek', created_at: `2026-09-29 12:0${i}:00`,
  }))
  await page.route('**/api/translate/history**', (r) => r.fulfill({ json: { items } }))
  await page.goto('/#/translate')
  const list = page.getByTestId('translate-history').locator('li')
  await expect(list).toHaveCount(5)
  await expect(list.first()).toContainText('Japanese → English · DeepSeek')
  await page.getByRole('button', { name: 'Show all (7)' }).click()
  await expect(list).toHaveCount(7)
  await page.getByRole('button', { name: 'Show fewer' }).click()
  await expect(list).toHaveCount(5)
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
