import { expect, test } from '@playwright/test'

import { guard, mockVoiceClone, openVoices } from './voiceCloneMocks'
import { shot } from './shots'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project (390x844, touch): Review > Voices tab fits the screen
// and its controls are 44px targets.

test('voices panel on a phone: no sideways scroll, 44px targets', async ({ page }) => {
  const unmocked = await guard(page)
  const m = await mockVoiceClone(page)
  const panel = await openVoices(page)
  const wei = panel.getByRole('listitem', { name: 'Voice for SPEAKER_00' })
  await expect(wei.getByTestId('clone-warning')).toBeVisible()

  await wei.getByRole('button', { name: 'Find clips in the audio' }).tap()
  m.state.jobDone = true
  await expect(wei.getByRole('button', { name: 'Use clip 1' })).toBeVisible()
  await expectNoSidewaysScroll(page)

  // (i) help buttons are small on purpose; their ::after widens the target (index.css).
  const small = await panel.locator('button:not(.link, .field-help-btn), select, input:not([type=file])').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, what: e.getAttribute('aria-label') ?? e.textContent }))
      .filter((x) => x.h < 44))
  expect(small).toEqual([])
  await shot(page, 'voice-clone-phone-candidates', true)
  await wei.scrollIntoViewIfNeeded()
  await shot(page, 'voice-clone-phone-card')
  expect(unmocked).toEqual([])
})
