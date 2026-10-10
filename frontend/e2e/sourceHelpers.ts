import { expect, type Page } from '@playwright/test'

// The Whisper model sits beside Source language; Speakers and Advanced are
// their own folds. Wait for the saved options to load (the settings summary
// shows then) so a click on a fold is not undone by the first render.
export async function openTranscribeOptions(page: Page) {
  await expect(page.getByTestId('settings-summary')).toBeVisible()
}

// Turns Developer Mode on for this page: the Transcribe knobs it gates (beam size, VAD threshold and so on) are
// only in the DOM then. The setting is server-side and PC-only, so the read is answered here.
export async function enableDeveloperMode(page: Page) {
  await page.route('**/api/assistant/settings', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { developer_mode: true, engine: null, model: null, engine_choices: [] } })
      : route.fallback())
}
