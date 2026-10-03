import { expect, test } from '@playwright/test'

import { mockTranscription } from './transcriptionMissingMocks'

// A fresh install has no faster-whisper: the Transcribe card says so and links
// to the Install by task flow; once installed neither the note nor the block shows.

test('not installed: the card says so, and Diagnostics leads with the same install', async ({ page }) => {
  const m = await mockTranscription(page, false)
  await page.goto('/#/drama/1/source')
  const note = page.locator('#transcribe-not-installed')
  await expect(note).toContainText("Transcription isn't installed yet.")

  await note.getByRole('link', { name: 'Install transcription' }).click()
  await expect(page).toHaveURL(/#\/diagnostics\?install=transcription/)
  const block = page.getByTestId('transcription-missing')
  await expect(block.getByRole('heading', { name: "Transcription isn't installed yet" })).toBeVisible()
  await expect(block).toContainText('approx. 80 MB to download')
  // The existing two-press confirmation; nothing is sent by the first press.
  await block.getByRole('button', { name: 'Install for Transcribe speech (Whisper)' }).click()
  await expect(page.getByRole('button', { name: 'Confirm install 1 package (approx. 80 MB)' })).toBeVisible()
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
