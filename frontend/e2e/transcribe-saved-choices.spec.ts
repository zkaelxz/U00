import { expect, test } from '@playwright/test'

import { selectMediaTab } from './mediaTabs'
import { openAdvanced, openTranscribeOptions, transcribeCard } from './sourceHelpers'

// The Transcribe tab's language and run choices (speaker detection, names, prompt) are kept on the title, so a
// browser with nothing stored, the Queue and the next visit start from what was set. The config route is answered
// here and remembers a save, so the shared seeded library is not written.
async function mockSavedConfig(page: import('@playwright/test').Page) {
  const saves: Record<string, unknown>[] = []
  let saved: Record<string, unknown> = {}
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch({ method: 'GET' })
    const cfg = await resp.json()
    if (route.request().method() === 'POST') {
      saves.push(route.request().postDataJSON() as Record<string, unknown>)
      saved = { ...saved, ...saves[saves.length - 1] }
    }
    return route.fulfill({ response: resp, json: { ...cfg, ...saved } })
  })
  return saves
}

test('language and speaker detection are saved with the title and survive a browser with nothing stored', async ({ page }) => {
  const saves = await mockSavedConfig(page)
  await page.goto('/#/drama/1/source')
  await openTranscribeOptions(page)
  const card = transcribeCard(page)
  await card.getByLabel('Source language').selectOption('ja')
  await card.getByRole('switch', { name: 'Detect speakers' }).click()
  await expect(card.getByTestId('run-settings')).toContainText('Japanese')
  await expect(card.getByTestId('run-settings')).toContainText('speaker detection on')
  await expect(card.getByTestId('run-settings-saved')).toContainText('Not saved to this title yet')

  const advanced = await openAdvanced(page)
  await advanced.getByRole('button', { name: 'Save options' }).click()
  await expect.poll(() => saves.length).toBe(1)
  expect(saves[0]).toMatchObject({ source_language: 'ja', run_start: { run_diarize: true } })
  await selectMediaTab(page, 'transcribe')
  await expect(card.getByTestId('run-settings-saved')).toContainText('Saved with this title')

  // Another browser: nothing in local storage.
  await page.evaluate(() => window.localStorage.clear())
  await page.reload()
  await openTranscribeOptions(page)
  await expect(card.getByLabel('Source language')).toHaveValue('ja')
  await expect(card.getByRole('switch', { name: 'Detect speakers' })).toBeChecked()
  await expect(card.getByTestId('run-settings-saved')).toContainText('Saved with this title')
})
