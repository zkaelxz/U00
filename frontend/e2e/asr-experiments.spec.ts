import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Steps 101/103/104: Settings > Transcription experiments, the MOSS choice in
// Transcribe > Advanced, and the "where did speaker detection run" note.
// Settings hit the real (seeded) API; every change is restored at the end.

test('transcription experiments save, and MOSS appears as a backend only while on', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Transcription experiments' })
  const batch = card.getByLabel('Qwen3-ASR batch size', { exact: true })
  await expect(batch).toHaveValue('1')
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/api/settings/asr-options') && r.request().method() === 'POST')

  await batch.fill('20')
  await expect(card.getByText('A whole number from 1 to 16.')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Save batch size' })).toBeDisabled()
  await batch.fill('4')
  await Promise.all([saved(), card.getByRole('button', { name: 'Save batch size' }).click()])
  await page.reload()
  await expect(page.getByRole('region', { name: 'Transcription experiments' }).getByLabel('Qwen3-ASR batch size', { exact: true })).toHaveValue('4')

  const moss = page.getByRole('switch', { name: 'MOSS-Transcribe-Diarize (experimental)' })
  await expect(moss).toBeChecked({ checked: false })
  await Promise.all([saved(), moss.click()])
  await expect(moss).toBeChecked()

  await page.goto('/#/drama/1/source')
  await page.locator('details.section', { hasText: 'Advanced' }).first().locator(':scope > summary').click()
  const backend = page.getByLabel('ASR backend', { exact: true })
  await expect(backend.locator('option', { hasText: 'MOSS-Transcribe-Diarize (experimental)' })).toHaveCount(1)

  // Restore both settings.
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card2 = page.getByRole('region', { name: 'Transcription experiments' })
  await card2.getByLabel('Qwen3-ASR batch size', { exact: true }).fill('1')
  await Promise.all([saved(), card2.getByRole('button', { name: 'Save batch size' }).click()])
  await Promise.all([saved(), page.getByRole('switch', { name: 'MOSS-Transcribe-Diarize (experimental)' }).click()])

  await page.goto('/#/drama/1/source')
  await page.locator('details.section', { hasText: 'Advanced' }).first().locator(':scope > summary').click()
  await expect(page.getByLabel('ASR backend', { exact: true }).locator('option', { hasText: 'MOSS' })).toHaveCount(0)
})

test('Speakers says where the last speaker detection ran', async ({ page }) => {
  await page.route('**/api/diarization/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, hf_token_configured: true, expected_speakers: null, min_speakers: 2,
        max_speakers: 4, last_device: 'cuda', audio_available: true,
      },
    }))
  await page.goto('/#/drama/1/source')
  await page.locator('details.section', { hasText: 'Speakers' }).first().locator(':scope > summary').click()
  await expect(page.getByTestId('diarize-device')).toHaveText('Last Detect speakers run (pyannote) used the GPU.')
})
