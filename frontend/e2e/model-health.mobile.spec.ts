import { expect, test } from '@playwright/test'

import { guardWrites, status } from './modelHealthMocks'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Model health card on a phone (390x844, touch): no sideways scroll, 44px
// targets for its buttons and links, and the two-step preset switch.
// Every /api/models call that isn't a GET is mocked (catch-all guard).
// Set MODEL_HEALTH_SHOTS_DIR=<dir> to save phone screenshots.

const SHOTS = process.env.MODEL_HEALTH_SHOTS_DIR

// The card is a fold that opens itself only on a problem; these specs need it open.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.section.diagnostics.modelHealth', '1'))
})

test('phone: the card fits, targets are 44px, and a switch asks twice', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/models/status', (r) => r.fulfill({ json: status() }))
  let posted = 0
  await page.route('**/api/models/presets/*/switch', (r) => {
    posted += 1
    return r.fulfill({ json: { preset_id: 9, engine: 'deepseek', from_model: 'deepseek-chat', to_model: 'deepseek-v4-flash' } })
  })
  await page.goto('/#/diagnostics')
  const card = page.getByRole('region', { name: 'Model health' })
  await expect(card.getByTestId('model-health-badge')).toHaveText('2 warnings')
  await card.scrollIntoViewIfNeeded()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  const targets = card.locator('ul.model-list').first().locator('button, a').or(card.locator('.actions button'))
  const n = await targets.count()
  expect(n).toBeGreaterThanOrEqual(4)
  for (let i = 0; i < n; i++) {
    const box = await targets.nth(i).boundingBox()
    expect(box, `target ${i}`).not.toBeNull()
    expect(await hitHeight(targets.nth(i)), `target ${i} height`).toBeGreaterThanOrEqual(44)
    expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  }
  const fold = card.locator('summary', { hasText: 'Other configured models' })
  expect((await hitHeight(fold))).toBeGreaterThanOrEqual(44)

  if (SHOTS) {
    await card.screenshot({ path: `${SHOTS}/model-health-phone-card.png` })
    await page.screenshot({ path: `${SHOTS}/model-health-phone-390x844.png` })
  }

  const row = card.locator('li', { hasText: 'Preset: Old DeepSeek' })
  await row.getByRole('button', { name: 'Switch Preset: Old DeepSeek to deepseek-v4-flash' }).tap()
  expect(posted).toBe(0)
  const confirm = row.getByRole('button', { name: 'Confirm switch to deepseek-v4-flash' })
  expect((await hitHeight(confirm))).toBeGreaterThanOrEqual(44)
  if (SHOTS) await card.screenshot({ path: `${SHOTS}/model-health-phone-confirm.png` })
  await confirm.tap()
  await expect(card.getByTestId('model-health-notice')).toHaveText('Preset: Old DeepSeek now uses deepseek-v4-flash.')
  expect(posted).toBe(1)
  expect(unmocked).toEqual([])
})
