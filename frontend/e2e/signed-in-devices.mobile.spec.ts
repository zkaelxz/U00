import { expect, test } from '@playwright/test'

import { mockDevices } from './deviceSessionsMocks'
import { openSettingsGroups } from './settingsNav'

// Phone (390x844): the lost-phone flow keeps 44px targets and the page does not scroll sideways.

test('signed-in devices fit a phone with 44px targets', async ({ page }) => {
  const s = await mockDevices(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Signed-in devices' })
  const name = 'Chrome on Android (network 198.51.100.x)'
  const first = card.getByRole('button', { name: `Sign out ${name}` })
  await expect(first).toBeVisible()
  for (const b of [first, card.getByRole('button', { name: 'Sign out all other devices' })]) {
    expect((await b.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await first.click()
  const confirm = card.getByRole('button', { name: `Confirm sign out ${name}` })
  expect((await confirm.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await confirm.click()
  await expect(card).toContainText('Signed out Chrome on Android.')
  // Polled: the header's account menu is briefly wider while the page settles.
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth), { message: 'page scrolls sideways' })
    .toBeLessThanOrEqual(0)
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/signed-in-devices-phone.png`, fullPage: true })
  expect(s.unmocked).toEqual([])
})
