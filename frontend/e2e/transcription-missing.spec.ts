import { expect, test } from '@playwright/test'

import { mockTranscription } from './transcriptionMissingMocks'

// A fresh install has no faster-whisper: the Transcribe card shows the preflight card with the
// install inline; once installed neither the card nor the Diagnostics block shows.

// A config request still in flight when a test ends would call route.fulfill on a
// disposed response; let those handlers finish and ignore their errors.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('not installed: the card says so and offers the install in place', async ({ page }) => {
  const m = await mockTranscription(page, false)
  await page.goto('/#/drama/1/source')
  const card = page.locator('#transcribe-not-installed').getByRole('region', { name: 'Before you run' })
  await expect(card.getByTestId('preflight-whisper')).toContainText('Not installed')
  // Disabled up front: the card is the reason, so nothing can start and fail with a raw error.
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeDisabled()
  // The two-press confirmation; nothing is sent by the first press.
  await card.getByRole('button', { name: /Install \(about 80 MB\)/ }).click()
  await expect(card.getByRole('button', { name: 'Confirm install 1 package (approx. 80 MB)' })).toBeVisible()
  expect(m.sent).toEqual([])
})

test('installed: no note on the card and no block in Diagnostics', async ({ page }) => {
  await mockTranscription(page, true)
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('button', { name: 'Transcribe', exact: true })).toBeVisible()
  await expect(page.locator('#transcribe-not-installed')).toHaveCount(0)
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('dependency-panel')).toBeVisible()
  await expect(page.getByTestId('transcription-missing')).toHaveCount(0)
})
