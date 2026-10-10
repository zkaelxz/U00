import { expect, test } from '@playwright/test'

import { enableDeveloperMode, openTranscribeOptions } from './sourceHelpers'

// Basic Transcribe: the everyday controls up front, one "More options" fold, and the seven
// developer knobs only in the DOM when Developer Mode is on.
const KNOBS = ['Beam size', 'VAD threshold', 'Hallucination guard', 'Separation backend', 'Hardsub OCR', 'Hardsub interval', 'Whisper repeat guard']

test('with Developer Mode off the seven knobs are not in the DOM', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = page.getByRole('region', { name: 'Transcribe' })
  await card.locator('.section-title', { hasText: /^More options$/ }).click()
  await expect(card.getByLabel('Expected speakers', { exact: true })).toBeVisible()
  for (const k of KNOBS) await expect(card.getByLabel(k, { exact: true })).toHaveCount(0)
  await expect(card.getByLabel('Sensitivity', { exact: true })).toBeVisible()
})

test('a developer value changed with the mode on is still saved after the mode turns off', async ({ page }) => {
  const saves: Record<string, unknown>[] = []
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    // Read with GET: route.fetch() would forward the POST and save into the shared library.
    const resp = await route.fetch({ method: 'GET' })
    const cfg = await resp.json()
    if (route.request().method() !== 'POST') return route.fulfill({ response: resp, json: cfg })
    saves.push(route.request().postDataJSON() as Record<string, unknown>)
    return route.fulfill({ response: resp, json: { ...cfg, ...saves[saves.length - 1] } })
  })
  await enableDeveloperMode(page)
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = page.getByRole('region', { name: 'Transcribe' })
  await card.locator('.section-title', { hasText: /^More options$/ }).click()
  await card.getByLabel('Beam size', { exact: true }).fill('8')
  await page.unroute('**/api/assistant/settings')
  await page.route('**/api/assistant/settings', (r) => r.fulfill({ json: { developer_mode: false, engine: null, model: null, engine_choices: [] } }))
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('baihe:developer-mode', { detail: { on: false } })))
  await expect(card.getByLabel('Beam size', { exact: true })).toHaveCount(0)
  await card.getByRole('button', { name: 'Save options' }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0]).toMatchObject({ beam_size: 8 })
  // The fold's summary shows only while it is closed.
  await card.locator('.section-title', { hasText: /^More options$/ }).click()
  await expect(card.locator('details.section > summary').filter({ hasText: 'More options' }).first()).toContainText('1 developer option changed')
})
