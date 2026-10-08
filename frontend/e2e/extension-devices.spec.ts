import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { NEW_TOKEN, mockExtensionDevices } from './extensionDevicesMocks'
import { openSettingsGroups } from './settingsNav'

// Settings > Browser extension devices (desktop). Every device-token call is mocked.

const TITLE = 'Browser extension devices'
const member = { ...ME.signedIn, permissions: [...ME.signedIn.permissions, 'extension.send'] }

test('adds a device, shows its token once with a copy button, then hides it for good', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'baihe_csrf', value: 'csrf-ext', url: baseURL! }])
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const s = await mockExtensionDevices(page, member)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: TITLE })
  await expect(card).toContainText('Home desktop')
  await expect(card).toContainText('Last used 2 hours ago · network 198.51.100.x')
  await expect(card).toContainText('Revoked yesterday')

  await card.getByRole('button', { name: 'Add device' }).click()
  await expect(card).toContainText('Name the device')
  expect(s.sent).toHaveLength(0)
  await card.getByLabel('Device name').fill('  Work   laptop ')
  await card.getByLabel('Expires').selectOption({ label: '1 year' })
  await card.getByRole('button', { name: 'Add device' }).click()

  const shown = card.getByRole('group', { name: 'New token for Work laptop' })
  await expect(shown.getByRole('textbox', { name: 'Token for Work laptop' })).toHaveValue(NEW_TOKEN)
  await expect(shown).toContainText('It is shown only once')
  await expect(card.getByRole('button', { name: 'Add device' })).toHaveCount(0) // one token on screen at a time
  await shown.getByRole('button', { name: 'Copy' }).click()
  await expect(shown).toContainText('Copied.')
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(NEW_TOKEN)
  await shown.getByRole('button', { name: 'Done' }).click()
  await expect(shown).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(NEW_TOKEN)
  await expect(card).toContainText('Never used')

  expect(s.sent.map((r) => `${r.method()} ${new URL(r.url()).pathname}`)).toEqual(['POST /api/auth/device-tokens'])
  expect(s.sent[0].postDataJSON()).toEqual({ label: 'Work   laptop', expires_in_days: 365 })
  expect(s.sent[0].headers()['x-csrf-token']).toBe('csrf-ext')
  expect(s.unmocked).toEqual([])
})

test('revokes a device after a confirm step', async ({ page }) => {
  const s = await mockExtensionDevices(page, member)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: TITLE })
  await card.getByRole('button', { name: 'Revoke Home desktop' }).click()
  expect(s.sent).toHaveLength(0)
  await card.getByRole('button', { name: 'Confirm revoke Home desktop' }).click()
  await expect(card).toContainText('Revoked Home desktop.')
  await expect(card.getByRole('button', { name: /Revoke/ })).toHaveCount(0)
  expect(s.sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/auth/device-tokens/21/revoke'])
  expect(s.unmocked).toEqual([])
})

test('without extension.send the list shows but adding explains why', async ({ page }) => {
  const s = await mockExtensionDevices(page, ME.signedIn)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: TITLE })
  await expect(card).toContainText('Home desktop')
  await expect(card).toContainText('extension.send')
  await expect(card.getByRole('button', { name: 'Add device' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test("the owner at the PC sees everyone's devices and can revoke any", async ({ page }) => {
  const s = await mockExtensionDevices(page, { ...ME.authOff, permissions: ['admin.settings', 'admin.users'] })
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: TITLE })
  const all = card.getByTestId('all-extension-devices')
  await expect(all).toContainText('Home desktop (Jane)')
  await expect(card.getByLabel('Device name')).toHaveCount(0) // the owner has no account of their own
  await all.getByRole('button', { name: 'Revoke Home desktop (Jane)' }).click()
  await all.getByRole('button', { name: 'Confirm revoke Home desktop (Jane)' }).click()
  await expect(all).toContainText('Revoked Home desktop (Jane).')
  expect(s.sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/admin/device-tokens/21/revoke'])
  expect(s.unmocked).toEqual([])
})

test('hidden from the owner without admin.users', async ({ page }) => {
  await mockExtensionDevices(page, ME.authOff)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  await expect(page.getByRole('region', { name: 'Sharing' })).toBeVisible()
  await expect(page.getByRole('region', { name: TITLE })).toHaveCount(0)
})
