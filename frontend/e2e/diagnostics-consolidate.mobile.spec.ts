import { expect, test } from '@playwright/test'
import { diagSection, openDiagTab } from './diagnosticsTabsNav'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { installHitArea } from './hitArea'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Diagnostics panes fit.

test('Setup with models, speaker detection and a task fit a phone with 44px targets', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false, gpuState: 'cpu_on_gpu' })
  await page.goto('/#/diagnostics')
  await expect(diagSection(page, /^Setup/)).toBeVisible()
  await openDiagTab(page, 'Models on disk')
  await expect(diagSection(page, /^Models$/).getByRole('list', { name: 'Whisper (faster-whisper) downloads' })).toBeVisible()
  await openDiagTab(page, 'Speaker detection')
  await expect(page.getByRole('button', { name: 'Check access online' })).toBeVisible()
  await openDiagTab(page, 'Packages and installs')
  await page.getByTestId('task-details-hardsub_ocr').locator('summary').click()
  await expectNoSidewaysScroll(page)
  const small = await page.locator('button:not(.field-help-btn):not(.toggle), summary, a.btn').evaluateAll((els) =>
    els.filter((e) => {
      const r = e.getBoundingClientRect()
      return r.width > 0 && r.height > 0 && window.hitHeight(e) < 43.5
    }).map((e) => `${e.tagName} ${(e.textContent ?? '').trim().slice(0, 30)}`))
  expect(small).toEqual([])
  expect(unmocked).toEqual([])
})

test('model downloads name their model and wrap without sideways scroll on a phone', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { cache: { hf_cache: [], hf_total_bytes: 0, model_files: [], model_files_total_bytes: 0 } })
  await page.route('**/api/diagnostics/model-downloads', (r) => r.fulfill({
    json: { models: [
      ['whisper_large_v3', 'Whisper large-v3 (about 3 GB)'],
      ['whisper_breeze_asr_25', 'Breeze-ASR-25, Chinese (about 3 GB)'],
    ].map(([id, label]) => ({ id, label, downloaded: false, job_id: `model-download:${id}` })) },
  }))
  await page.route('**/api/jobs/**', (r) => r.fulfill({ status: 404, json: { detail: 'No such job.' } }))
  await page.goto('/#/diagnostics')
  await openDiagTab(page, 'Models on disk')
  await expect(page.getByRole('button', { name: 'Download Breeze-ASR-25, Chinese (about 3 GB)' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download Whisper large-v3 (about 3 GB)' })).toBeVisible()
  await expectNoSidewaysScroll(page)
  await page.screenshot({ path: test.info().outputPath('models-downloads-phone.png'), fullPage: true })
})
