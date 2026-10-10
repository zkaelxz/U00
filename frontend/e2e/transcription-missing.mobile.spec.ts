import { expect, test } from '@playwright/test'

import { mockTranscription } from './transcriptionMissingMocks'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone: the preflight card and the Diagnostics block fit (no sideways scroll, 44 px targets).

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: the preflight card stacks and its install button fits the screen', async ({ page }) => {
  await mockTranscription(page, false)
  await page.goto('/#/drama/1/source')
  const install = page.locator('#transcribe-not-installed').getByRole('button', { name: /Install \(about 80 MB\)/ })
  await expect(install).toBeVisible()
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeDisabled()
  expect((await hitHeight(install))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)

  await page.goto('/#/diagnostics?install=transcription')
  const block = page.getByTestId('transcription-missing').getByRole('button', { name: 'Install for Transcribe speech (Whisper)' })
  await expect(block).toBeVisible()
  expect((await hitHeight(block))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)
})
