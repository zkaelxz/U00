import { expect, type Page } from '@playwright/test'

// The Whisper model sits beside Source language; Speakers and Advanced are
// their own folds. Wait for the saved options to load (the settings summary
// shows then) so a click on a fold is not undone by the first render.
export async function openTranscribeOptions(page: Page) {
  await expect(page.getByTestId('settings-summary')).toBeVisible()
}
