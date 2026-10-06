import { expect, test } from '@playwright/test'
import { editPauseAndSave, openAdvanced } from './minPauseHelpers'

// The "Pause that can split a long line" field in Transcribe > Advanced
// (desktop; min-pause.mobile.spec.ts runs the same edit on a phone).

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('editing the pause and saving sends min_pause_sec', async ({ page }) => {
  expect((await editPauseAndSave(page)).min_pause_sec).toBe(0.6)
})

test('an out-of-range pause is refused before saving', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  await page.getByRole('spinbutton', { name: 'Pause that can split a long line' }).fill('2.5')
  await page.getByRole('button', { name: 'Save options' }).click()
  await expect(page.getByText(/must be between 0.1 and 2/)).toBeVisible()
})
