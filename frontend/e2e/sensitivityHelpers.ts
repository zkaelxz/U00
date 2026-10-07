import { expect, type Page } from '@playwright/test'
import { openAdvanced } from './minPauseHelpers'

// Picks "More sensitive" in Transcribe > Advanced and saves; the config route
// is mocked so the shared seeded library is not changed. Returns the body the
// server received.
export async function pickSensitiveAndSave(page: Page): Promise<Record<string, unknown>> {
  let saved = ''
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    const cfg = await resp.json()
    if (route.request().method() === 'GET') {
      await route.fulfill({ response: resp, json: { ...cfg, sensitivity_preset: 'normal' } })
      return
    }
    saved = route.request().postData() ?? ''
    await route.fulfill({ response: resp, json: { ...cfg, ...JSON.parse(saved) } })
  })
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  const select = page.getByLabel('Sensitivity', { exact: true })
  await expect(select).toHaveValue('normal')
  await expect(page.getByText('Catches quieter or faster speech, but may add false text on music or breathing.')).toBeAttached()
  await select.selectOption('sensitive')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
  return JSON.parse(saved)
}
