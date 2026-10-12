import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { openTranscribeOptions, openAdvanced } from './sourceHelpers'

// Steps 101/103: Settings > System > Advanced transcription and the "where did speaker detection run" note.
// Settings hit the real (seeded) API; every change is restored at the end.

// The batch size is a Developer Mode field; the seeded library has the mode off, so the read says it is on.
async function developerModeOn(page: Page) {
  await page.route('**/api/assistant/settings', async (route) => {
    const resp = await route.fetch()
    return route.fulfill({ response: resp, json: { ...(await resp.json()), developer_mode: true } })
  })
}

test('the batch size is hidden until Developer Mode is on', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const card = page.getByRole('region', { name: 'Advanced transcription' })
  await expect(card.getByLabel('Mixed languages', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Qwen3-ASR batch size', { exact: true })).toHaveCount(0)
})

test('advanced transcription saves, and MOSS is not offered', async ({ page }) => {
  await developerModeOn(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const card = page.getByRole('region', { name: 'Advanced transcription' })
  const batch = card.getByLabel('Qwen3-ASR batch size', { exact: true })
  await expect(batch).toHaveValue('1')
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/api/settings/asr-options') && r.request().method() === 'POST')

  await batch.fill('20')
  await expect(card.getByText('A whole number from 1 to 16.')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Save batch size' })).toBeDisabled()
  await batch.fill('4')
  await Promise.all([saved(), card.getByRole('button', { name: 'Save batch size' }).click()])
  await page.reload()
  await openSettingsGroups(page, 'System')
  await expect(page.getByRole('region', { name: 'Advanced transcription' }).getByLabel('Qwen3-ASR batch size', { exact: true })).toHaveValue('4')

  await expect(page.getByRole('switch', { name: /MOSS/ })).toHaveCount(0)

  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  await openAdvanced(page)
  const backend = page.getByLabel('ASR backend', { exact: true })
  await expect(backend.locator('option', { hasText: 'MOSS' })).toHaveCount(0)

  // Restore the batch size.
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const card2 = page.getByRole('region', { name: 'Advanced transcription' })
  await card2.getByLabel('Qwen3-ASR batch size', { exact: true }).fill('1')
  await Promise.all([saved(), card2.getByRole('button', { name: 'Save batch size' }).click()])
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
  await openTranscribeOptions(page)
  await openAdvanced(page)
  await expect(page.getByTestId('diarize-device')).toHaveText('Last Detect speakers run (pyannote) used the GPU.')
})
