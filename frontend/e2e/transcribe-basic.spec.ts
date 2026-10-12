import { expect, test } from '@playwright/test'

import { enableDeveloperMode, openAdvanced, openTranscribeOptions, transcribeCard } from './sourceHelpers'

// Basic Transcribe: the everyday controls on the Transcribe tab, the Advanced options folds on Tools, and the seven
// developer knobs only in the DOM when Developer Mode is on.
const KNOBS = ['Beam size', 'VAD threshold', 'Hallucination guard', 'Separation backend', 'Hardsub OCR', 'Hardsub interval', 'Whisper repeat guard']

test('with Developer Mode off the seven knobs are not in the DOM', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = await openAdvanced(page)
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
  const card = await openAdvanced(page)
  await card.getByLabel('Beam size', { exact: true }).fill('8')
  await page.unroute('**/api/assistant/settings')
  await page.route('**/api/assistant/settings', (r) => r.fulfill({ json: { developer_mode: false, engine: null, model: null, engine_choices: [] } }))
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('baihe:developer-mode', { detail: { on: false } })))
  await expect(card.getByLabel('Beam size', { exact: true })).toHaveCount(0)
  await card.getByRole('button', { name: 'Save options' }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0]).toMatchObject({ beam_size: 8 })
  // The section's one-line summary says what was changed, with the hidden knobs counted.
  await expect(card.getByTestId('advanced-summary')).toContainText('1 developer option changed')
})

test('a Whisper model not recommended for the language is disabled with its reason, and the saved fallback is announced', async ({ page }) => {
  const reason = 'Whisper medium isn’t recommended for Japanese: in our tests it made about 1.9 points more errors than large-v3-turbo and was slower.'
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    const cfg = await resp.json()
    if (route.request().method() !== 'GET') return route.fulfill({ response: resp, json: cfg })
    return route.fulfill({
      response: resp,
      json: { ...cfg, whisper_exclusions: { medium: { ja: reason } }, whisper_size_notice: 'This title was saved with medium. It now uses large-v3-turbo.' },
    })
  })
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await expect(card.getByTestId('whisper-size-notice')).toContainText('saved with medium')
  await card.getByLabel('Source language').selectOption('ja')
  const medium = card.getByLabel('Whisper model').locator('option', { hasText: /^medium/ })
  await expect(medium).toBeDisabled()
  await expect(medium).toHaveText(/not for Japanese/)
  await expect(card.getByText(reason)).toBeVisible()
  await card.getByLabel('Source language').selectOption('ko')
  await expect(card.getByLabel('Whisper model').locator('option', { hasText: /^medium/ })).toBeEnabled()
})
