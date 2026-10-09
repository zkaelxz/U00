import { test } from '@playwright/test'
import { expectStackedLayout } from './voiceDetectorLayout'

// Phone width: a single column, so the stacked notes are taller than on desktop but must not overflow.
for (const state of ['not-downloaded', 'failed', 'downloaded', 'standard'] as const) {
  test(`Voice detector fits the phone: ${state}`, async ({ page }) => {
    await expectStackedLayout(page, state, 400)
  })
}
