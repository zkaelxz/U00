import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { NEW_TOKEN, mockExtensionDevices } from './extensionDevicesMocks'
import { openSettingsGroups } from './settingsNav'

// Phone (390x844): adding and revoking an extension device keeps 44px targets and no sideways scroll.

test('extension devices fit a phone with 44px targets', async ({ page }) => {
  const s = await mockExtensionDevices(page, { ...ME.signedIn, permissions: [...ME.signedIn.permissions, 'extension.send'] })
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Browser extension devices' })
  const revoke = card.getByRole('button', { name: 'Revoke Home desktop' })
  const add = card.getByRole('button', { name: 'Add device' })
  for (const b of [revoke, add, card.getByLabel('Device name'), card.getByLabel('Expires')]) {
    await expect(b).toBeVisible()
    expect((await b.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await card.getByLabel('Device name').fill('Phone test')
  await add.click()
  const shown = card.getByRole('group', { name: 'New token for Phone test' })
  await expect(shown.getByRole('textbox')).toHaveValue(NEW_TOKEN)
  for (const name of ['Copy', 'Done']) {
    expect((await shown.getByRole('button', { name }).boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await expect.poll(() => page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth), { message: 'page scrolls sideways' })
    .toBeLessThanOrEqual(0)
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/extension-devices-phone.png`, fullPage: true })
  await shown.getByRole('button', { name: 'Done' }).click()
  await revoke.click()
  const confirm = card.getByRole('button', { name: 'Confirm revoke Home desktop' })
  expect((await confirm.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await confirm.click()
  await expect(card).toContainText('Revoked Home desktop.')
  expect(s.unmocked).toEqual([])
})
