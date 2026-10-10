import { test } from '@playwright/test'
import { expectStackedLayout } from './voiceDetectorLayout'

// The field once rendered its notes in the select's flex row, one letter per line.
for (const state of ['not-downloaded', 'failed', 'downloaded', 'standard'] as const) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`Voice detector stacks cleanly: ${state}, ${colorScheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme })
      await expectStackedLayout(page, state, 300)
    })
  }
}
