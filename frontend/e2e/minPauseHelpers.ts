import { expect, type Page } from '@playwright/test'
import { openFoldFor } from './reviewFolds'
import { openTranscribeOptions } from './sourceHelpers'

export async function openAdvanced(page: Page) {
  await openFoldFor(page, 'More options')
  await openTranscribeOptions(page)
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^More options$/ }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

// Edits the pause field and saves; the config route is mocked so the shared
// seeded library is not changed. Returns the body the server received.
export async function editPauseAndSave(page: Page): Promise<Record<string, unknown>> {
  let saved = ''
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    // Always read with GET: route.fetch() would forward the POST and save into the shared library.
    const resp = await route.fetch({ method: 'GET' })
    const cfg = await resp.json()
    if (route.request().method() === 'GET') {
      await route.fulfill({ response: resp, json: { ...cfg, min_pause_sec: 0.35 } })
      return
    }
    saved = route.request().postData() ?? ''
    await route.fulfill({ response: resp, json: { ...cfg, ...JSON.parse(saved) } })
  })
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  const field = page.getByRole('spinbutton', { name: 'Pause that can split a long line' })
  await expect(field).toHaveValue('0.35')
  await expect(page.getByText('Higher gives fewer, longer lines. Lower cuts more.')).toBeAttached()
  await field.fill('0.6')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
  await expect(field).toHaveValue('0.6')
  return JSON.parse(saved)
}
