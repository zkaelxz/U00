import { expect, type Page } from '@playwright/test'
import { openAdvanced } from './sourceHelpers'

export const DIFFICULT_HINT = 'Use when speakers scream, whisper or talk over each other; slower and may add a few false lines.'

// Turns "Difficult audio" on in Transcribe > Advanced and saves; the config route is mocked so the
// shared seeded library is not changed. Returns the body the server received.
export async function turnDifficultAudioOnAndSave(page: Page): Promise<Record<string, unknown>> {
  let saved = ''
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    // Always read with GET: route.fetch() would forward the POST and save into the shared library.
    const resp = await route.fetch({ method: 'GET' })
    const cfg = await resp.json()
    if (route.request().method() === 'GET') {
      await route.fulfill({ response: resp, json: { ...cfg, difficult_audio: false } })
      return
    }
    saved = route.request().postData() ?? ''
    await route.fulfill({ response: resp, json: { ...cfg, ...JSON.parse(saved) } })
  })
  await page.goto('/#/drama/1/source')
  const advanced = await openAdvanced(page)
  const toggle = advanced.getByRole('switch', { name: 'Difficult audio' })
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await expect(advanced.getByText(DIFFICULT_HINT)).toBeAttached()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByTestId('advanced-summary')).toContainText('difficult audio')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
  return JSON.parse(saved)
}
