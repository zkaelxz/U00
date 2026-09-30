import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { mockDevices } from './deviceSessionsMocks'

// Settings > Signed-in devices (desktop). Every /api/auth/sessions call is
// mocked; no real session is ever ended.

test('lists my devices, this one first, and signs one out after a confirm', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'baihe_csrf', value: 'csrf-devices', url: baseURL! }])
  const s = await mockDevices(page)
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Signed-in devices' })
  await expect(card).toContainText('A device is signed out after 14 days without use, and 30 days after it signed in.')
  const rows = card.getByRole('list', { name: 'Signed-in devices' }).locator('li')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('Safari on iPhone')
  await expect(rows.nth(0)).toContainText('This device')
  await expect(rows.nth(0)).toContainText('In use now')
  await expect(rows.nth(0).getByRole('button')).toHaveCount(0) // this device signs out from the menu
  await expect(rows.nth(1)).toContainText('Last used 2 hours ago')
  await expect(rows.nth(1)).toContainText('network 198.51.100.x')
  await expect(rows.nth(2)).toContainText('network 2001:db8:1::/48')

  await rows.nth(1).getByRole('button', { name: 'Sign out Chrome on Android (network 198.51.100.x)' }).click()
  expect(s.sent).toHaveLength(0) // the first press only arms
  await rows.nth(1).getByRole('button', { name: 'Confirm sign out Chrome on Android (network 198.51.100.x)' }).click()
  await expect(card).toContainText('Signed out Chrome on Android.')
  await expect(rows).toHaveCount(2)
  expect(s.sent.map((r) => `${r.method()} ${new URL(r.url()).pathname}`)).toEqual(['POST /api/auth/sessions/12/revoke'])
  expect(s.sent[0].headers()['x-csrf-token']).toBe('csrf-devices')
  expect(s.unmocked).toEqual([])
})

test('signs out every other device, then the button explains why it is off', async ({ page }) => {
  const s = await mockDevices(page)
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Signed-in devices' })
  await card.getByRole('button', { name: 'Sign out all other devices' }).click()
  await card.getByRole('button', { name: 'Confirm sign out all other devices' }).click()
  await expect(card).toContainText('Signed out 2 other devices.')
  await expect(card.getByRole('list', { name: 'Signed-in devices' }).locator('li')).toHaveCount(1)
  await expect(card.getByRole('button', { name: 'Sign out all other devices' })).toBeDisabled()
  await expect(card).toContainText('No other devices are signed in.')
  expect(s.sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/auth/sessions/revoke-others'])
  expect(s.unmocked).toEqual([])
})

test('a device already gone shows the server reason and reloads the list', async ({ page }) => {
  const s = await mockDevices(page)
  s.refuse = { id: 13, status: 404, code: 'not_found', message: "That device isn't signed in." }
  await page.goto('/#/settings')
  const card = page.getByRole('region', { name: 'Signed-in devices' })
  const name = 'Edge on Windows (network 2001:db8:1::/48)'
  await card.getByRole('button', { name: `Sign out ${name}` }).click()
  await card.getByRole('button', { name: `Confirm sign out ${name}` }).click()
  await expect(card.getByRole('alert')).toContainText("That device isn't signed in.")
  await expect(card.getByRole('list', { name: 'Signed-in devices' }).locator('li')).toHaveCount(2)
  expect(s.unmocked).toEqual([])
})

test('hidden with sign-in off (the owner at the PC has no sessions)', async ({ page }) => {
  const s = await mockDevices(page, ME.authOff)
  await page.goto('/#/settings')
  await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Sharing' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Signed-in devices' })).toHaveCount(0)
  expect(s.sent).toEqual([])
})
