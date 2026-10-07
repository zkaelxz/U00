import { expect, test } from '@playwright/test'
import { pickSensitiveAndSave } from './sensitivityHelpers'

// The Sensitivity preset in Transcribe > Advanced (desktop; sensitivity.mobile.spec.ts runs it on a phone).

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('choosing More sensitive and saving sends sensitivity_preset', async ({ page }) => {
  expect((await pickSensitiveAndSave(page)).sensitivity_preset).toBe('sensitive')
})
