import { expect, test } from '@playwright/test'
import { turnDifficultAudioOnAndSave } from './difficultAudioHelpers'

// The Difficult audio switch in Transcribe > Advanced (desktop; difficult-audio.mobile.spec.ts runs it on a phone).

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('turning Difficult audio on and saving sends difficult_audio', async ({ page }) => {
  expect((await turnDifficultAudioOnAndSave(page)).difficult_audio).toBe(true)
})
