import { expect, type Page } from '@playwright/test'
import { openTranscribeOptions } from './sourceHelpers'

type State = 'not-downloaded' | 'failed' | 'downloaded' | 'standard'

const BASE = {
  qwen_asr_batch_size: 1, qwen_asr_batch_min: 1, qwen_asr_batch_max: 16, qwen_asr_version: null,
  qwen_asr_batching_available: false, qwen_vad_refine_timing: false, mixed_languages: false,
  voice_detector: 'auto', asmr_vad_onnxruntime_installed: true, asmr_vad_model_downloaded: false,
  asmr_vad_download_job_id: 'job-vad',
}

export async function openVoiceDetector(page: Page, state: State) {
  const options = {
    ...BASE,
    voice_detector: state === 'standard' ? 'standard' : 'auto',
    asmr_vad_model_downloaded: state === 'downloaded',
  }
  await page.route('**/api/settings/asr-options', (route) => route.fulfill({ json: options }))
  await page.route('**/api/jobs/job-vad', (route) =>
    route.fulfill({
      json: state === 'failed'
        ? { id: 'job-vad', status: 'error', error: 'The ASMR detector download failed. Check your connection and try again.' }
        : { id: 'job-vad', status: 'done' },
    }))
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await page.locator('.section-title', { hasText: /^Advanced$/ }).click()
  const select = page.getByLabel('Voice detector', { exact: true })
  await expect(select).toBeVisible()
  return select
}

// The select takes the cell's width, and the notes and button stack below it inside the cell.
export async function expectStackedLayout(page: Page, state: State, maxHeight: number) {
  const select = await openVoiceDetector(page, state)
  const cell = page.locator('.field-with-extras')
  const cellBox = (await cell.boundingBox())!
  const selectBox = (await select.boundingBox())!
  expect(selectBox.width, 'select fills the cell').toBeGreaterThan(cellBox.width * 0.9)
  expect(cellBox.height, 'field height').toBeLessThan(maxHeight)

  const below = state === 'failed'
    ? [page.getByTestId('voice-detector-download-problem')]
    : state === 'not-downloaded'
      ? [page.getByTestId('voice-detector-note'), page.getByRole('button', { name: /Download the ASMR detector/ })]
      : []
  for (const el of below) {
    await expect(el).toBeVisible()
    const box = (await el.boundingBox())!
    expect(box.y, 'sits below the select').toBeGreaterThanOrEqual(selectBox.y + selectBox.height - 1)
    expect(box.x + box.width, 'inside the cell').toBeLessThanOrEqual(cellBox.x + cellBox.width + 1)
  }
  if (state === 'not-downloaded') {
    expect((await below[0].boundingBox())!.width, 'status text is not squeezed').toBeGreaterThan(160)
  }
  if (state === 'standard' || state === 'downloaded') {
    await expect(page.getByTestId('voice-detector-note')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Download the ASMR detector/ })).toHaveCount(0)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}
