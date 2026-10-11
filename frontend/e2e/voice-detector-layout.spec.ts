import { test } from '@playwright/test'
import { expectInputsBesideLabels, expectSelectTextReadable, expectStackedLayout, openVoiceDetector } from './voiceDetectorLayout'

// The field once rendered its notes in the select's flex row, one letter per line.
for (const state of ['not-downloaded', 'failed', 'downloaded', 'standard'] as const) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`Voice detector stacks cleanly: ${state}, ${colorScheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme })
      await expectStackedLayout(page, state, 300)
    })
  }
}

for (const width of [360, 1280, 1700]) {
  for (const state of ['not-downloaded', 'standard'] as const) {
    test(`Transcribe options grid lines up at ${width}px: ${state}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const select = await openVoiceDetector(page, state)
      await expectInputsBesideLabels(page)
      await expectSelectTextReadable(select)
    })
  }
}
