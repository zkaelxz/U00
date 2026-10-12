import { test } from '@playwright/test'
import { expectInputsBesideLabels, expectRowInputsAligned, expectStackedLayout, openVoiceDetector } from './voiceDetectorLayout'

// Phone width: a single column, so the stacked notes are taller than on desktop but must not overflow.
for (const state of ['not-downloaded', 'failed', 'downloaded', 'standard'] as const) {
  test(`Voice detector fits the phone: ${state}`, async ({ page }) => {
    await expectStackedLayout(page, state, 400)
  })
}

test('Transcribe options grid lines up on the phone', async ({ page }) => {
  await openVoiceDetector(page, 'standard')
  await expectInputsBesideLabels(page)
  await expectRowInputsAligned(page.getByRole('region', { name: 'Advanced options' }))
})
