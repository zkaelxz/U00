import { expect, test } from '@playwright/test'

import { mockTranscription } from './transcriptionMissingMocks'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone: the not-installed note and the Diagnostics block fit (no sideways scroll, 44 px targets).

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: transcription note and install block fit the screen', async ({ page }) => {
  await mockTranscription(page, false)
  await page.goto('/#/drama/1/source')
  const link = page.locator('#transcribe-not-installed').getByRole('link', { name: 'Install transcription' })
  await expect(link).toBeVisible()
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeDisabled()
  expect((await hitHeight(link))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)

  await link.click()
  const install = page.getByTestId('transcription-missing').getByRole('button', { name: 'Install for Transcribe speech (Whisper)' })
  await expect(install).toBeVisible()
  expect((await hitHeight(install))).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(noSideScroll)).toBe(true)
})
