import { expect, test } from '@playwright/test'
import { openBenchTab } from './benchSections'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// The Lab's transcription form carries the per-title Transcribe options, folded under
// "Settings like a real run" and set to the app's defaults. Named lab-* so it sorts after
// diagnostics.spec.ts like the other Lab specs. No cases are seeded, so the estimate is
// refused by the real API; what is checked is the request the form sends.

test('Benchmark Lab: transcription options start at the app defaults and travel with the arm', async ({ page }) => {
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Run a benchmark')
  const card = page.getByRole('region', { name: 'Run a benchmark' })
  await card.getByLabel('Stage', { exact: true }).selectOption('transcription')

  const recogniser = card.getByLabel('Speech recogniser 1', { exact: true })
  await expect(recogniser.locator('option', { hasText: 'Qwen3-ASR' })).toHaveCount(1)
  await expect(recogniser.locator('option', { hasText: 'anime-whisper' })).toHaveCount(1)

  const fold = card.getByTestId('bench-real-settings')
  await expect(fold).not.toHaveAttribute('open', '')
  await fold.locator('summary').click()
  // What a title uses when nothing is saved, not transcribe_for_timing's 2000 ms.
  await expect(fold.getByLabel('Beam size', { exact: true })).toHaveValue('5')
  await expect(fold.getByLabel('Min silence', { exact: true })).toHaveValue('300')
  await expect(fold.getByLabel('VAD threshold', { exact: true })).toHaveValue('0.5')

  await fold.getByLabel('Beam size', { exact: true }).fill('1')
  await expect(fold.locator('summary')).toContainText('1 changed')

  const sent = page.waitForRequest((r) => r.url().endsWith('/api/benchmark/estimate'))
  await card.getByRole('button', { name: 'Estimate cost' }).click()
  const body = (await sent).postDataJSON()
  expect(body.configs[0].options).toEqual({ beam_size: 1 })

  await fold.getByRole('button', { name: /Back to the app/ }).click()
  await expect(fold.getByLabel('Beam size', { exact: true })).toHaveValue('5')
  await expect(fold.locator('summary')).not.toContainText('changed')
})

test('Benchmark Lab: a recogniser that ignores an option says so', async ({ page }) => {
  // The runner has no Qwen3 packages, so the real API greys the recogniser out; this test is
  // about the ignored-option note, not about what is installed.
  await page.route('**/api/benchmark/options', async (route) => {
    const response = await route.fetch()
    const data = await response.json()
    for (const b of data.transcription_backends ?? []) delete b.problem
    await route.fulfill({ response, json: data })
  })
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Run a benchmark')
  const card = page.getByRole('region', { name: 'Run a benchmark' })
  await card.getByLabel('Stage', { exact: true }).selectOption('transcription')
  await card.getByLabel('Speech recogniser 1', { exact: true }).selectOption('qwen3_asr_vad')
  const fold = card.getByTestId('bench-real-settings')
  await fold.locator('summary').click()
  await expect(fold.getByLabel('Beam size', { exact: true })).toBeDisabled()
  await expect(fold.getByLabel('Difficult audio', { exact: true })).toBeEnabled()
})
