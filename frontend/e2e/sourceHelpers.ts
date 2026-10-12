import { expect, type Page } from '@playwright/test'

import { mediaPanel, mediaTab, openAllFolds } from './mediaTabs'

// The Media stage's sub-tabs: the Transcribe tab holds the run form (language, model, the Transcribe button),
// the Tools tab the Advanced options folds, Auto-tune and Speech coverage. Wait for the saved options to load
// (the settings summary shows then) so a click is not undone by the first render.
export async function openTranscribeOptions(page: Page) {
  await mediaTab(page, 'transcribe').click()
  await expect(page.getByTestId('settings-summary')).toBeVisible()
}

/** The Transcribe tab's body. */
export const transcribeCard = (page: Page) => mediaPanel(page, 'transcribe')

/** Opens the Tools tab with the four Advanced options folds open, and returns that section. */
export async function openAdvanced(page: Page) {
  await mediaTab(page, 'tools').click()
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  await expect(advanced).toBeVisible()
  await openAllFolds(advanced)
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
