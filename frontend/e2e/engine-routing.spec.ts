import { expect, test, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Settings > "Which engine does what" (Step 36) against the real API on the
// seeded throwaway library. SHOTS_DIR, when set, receives desktop and phone
// screenshots of the card.
const SHOTS = process.env.SHOTS_DIR

const card = (page: Page) => page.getByRole('region', { name: 'Which engine does what' })
const task = (page: Page) => card(page).getByLabel('Line helpers for translation-only engines', { exact: true })
const saved = (page: Page) =>
  page.waitForResponse((r) => r.url().includes('/api/settings/engine-routing/capabilities/') && r.request().method() === 'POST')

test('choose an engine for a task, see it persist, and test the offline engine', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/settings')
  await expect(card(page)).toBeVisible()
  await expect(card(page).getByText(/one short real call/)).toBeVisible()

  // A task: pick DeepSeek, reload, still DeepSeek; then back to the default.
  await expect(task(page)).toHaveValue('')
  await Promise.all([saved(page), task(page).selectOption('deepseek')])
  await expect(task(page)).toHaveValue('deepseek')
  await page.reload()
  await expect(task(page)).toHaveValue('deepseek')
  await Promise.all([saved(page), task(page).selectOption('')])
  await expect(task(page)).toHaveValue('')
  await expect(page.getByTestId('task-llm.instructions').getByText('Default', { exact: true })).toBeVisible()

  // An engine that needs a key and has none can't be tested (the e2e server has no keys).
  await expect(page.getByTestId('engine-claude').getByText('Missing')).toBeVisible()
  await expect(card(page).getByRole('button', { name: 'Test Claude' })).toBeDisabled()

  // The offline test engine is free and needs no network.
  const row = page.getByTestId('engine-fake')
  await row.getByRole('button', { name: 'Test Fake' }).click()
  await expect(row.getByText('Working', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(row.getByText(/Tested just now/)).toBeVisible()

  if (SHOTS) {
    await card(page).scrollIntoViewIfNeeded()
    await card(page).screenshot({ path: `${SHOTS}/routing-desktop.png` })
  }
})

test('the card fits a phone with 44px controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/settings')
  await expect(task(page)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  for (const el of [task(page), card(page).getByRole('button', { name: 'Test Fake' })]) {
    expect(await hitHeight(el)).toBeGreaterThanOrEqual(44)
  }
  if (SHOTS) await card(page).screenshot({ path: `${SHOTS}/routing-phone.png` })
})

test('a refused change rolls the choice back and says it is PC only', async ({ page }) => {
  await page.route('**/api/settings/engine-routing/capabilities/**', (route) =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'forbidden', message: 'PC only.' } }),
    }),
  )
  await page.goto('/#/settings')
  await expect(task(page)).toHaveValue('')
  await task(page).selectOption('deepseek')
  await expect(card(page).getByText('This only works on the main PC.')).toBeVisible()
  await expect(task(page)).toHaveValue('')
  // The 403 switched the tab to remote: the controls are disabled with a note.
  await expect(card(page).getByText('Choosing engines, testing and setting keys is PC only.')).toBeVisible()
  await expect(task(page)).toBeDisabled()
  await expect(card(page).getByRole('button', { name: 'Test Fake' })).toBeDisabled()
  await page.evaluate(() => sessionStorage.removeItem('baihe.pcOnly'))
})

test('a failed Ollama test shows a plain summary with the raw error under Details', async ({ page }) => {
  const raw = "HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded [WinError 10061]"
  await page.route('**/api/settings/engine-routing/engines/ollama/test', async (route) => {
    const res = await page.request.get('/api/settings/engine-routing')
    const body = await res.json()
    const e = body.engines.find((x: { engine: string }) => x.engine === 'ollama')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...e, status: 'failed', last_test: { ok: false, tested_at: new Date().toISOString(), error: raw } }),
    })
  })
  await page.goto('/#/settings')
  const row = page.getByTestId('engine-ollama')
  await row.getByRole('button', { name: /^Test / }).click()
  await expect(row.getByText(/isn't running.*ollama\.com/)).toBeVisible()
  await expect(row.getByText(raw)).toBeHidden()
  await row.getByText('Details', { exact: true }).click()
  await expect(row.getByText(raw)).toBeVisible()
})
