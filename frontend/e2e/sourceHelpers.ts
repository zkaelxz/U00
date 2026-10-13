import { expect, type Page } from '@playwright/test'

import { mediaPanel, openAllFolds, selectMediaTab } from './mediaTabs'

// The Media stage's sub-tabs: the Transcribe tab holds the run form (language, model, the Transcribe button),
// the Tools tab the Advanced options folds, Auto-tune and Speech coverage. Wait for the saved options to load
// (the settings summary shows then) so a click is not undone by the first render.
export async function openTranscribeOptions(page: Page) {
  await selectMediaTab(page, 'transcribe')
  await expect(page.getByTestId('settings-summary')).toBeVisible()
}

/** The Transcribe tab's body. */
export const transcribeCard = (page: Page) => mediaPanel(page, 'transcribe')

/**
 * Opens the Tools tab with the Advanced options folds open, and returns that section. The prompt fold
 * (Extra names, Replacement prompt) is opened too unless `prompt` is false.
 */
export async function openAdvanced(page: Page, prompt = true) {
  await selectMediaTab(page, 'tools')
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  await expect(advanced).toBeVisible()
  await openAllFolds(advanced)
  const more = advanced.locator('details.prompt-fold')
  if (prompt && (await more.count()) && (await more.getAttribute('open')) === null) await more.locator(':scope > summary').click()
  return advanced
}

// Turns Developer Mode on for this page: the Transcribe knobs it gates (beam size, VAD threshold and so on) are
// only in the DOM then. The setting is server-side and PC-only, so the read is answered here.
export async function enableDeveloperMode(page: Page) {
  await page.route('**/api/assistant/settings', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { developer_mode: true, engine: null, model: null, engine_choices: [] } })
      : route.fallback())
}

/** Two animation frames: React has committed and run the effects of whatever the last response or event changed. */
export const nextFrames = (page: Page) =>
  page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
