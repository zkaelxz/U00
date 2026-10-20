import { expect, test, type Page, type Request } from '@playwright/test'

import { ME } from './authMocks'
import { mockDevices } from './deviceSessionsMocks'
import { openSettingsGroups } from './settingsNav'
import { guardWrites } from './mockHelpers'

// Settings > Your Ollama key and the People flag (desktop). Every key call is
// mocked; no real key is ever sent anywhere.

const TITLE = 'Your Ollama key'
const KEY = '0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOpQrStUvWx'
const member = { ...ME.signedIn, permissions: [...ME.signedIn.permissions, 'jobs.start'] }

async function mockKey(page: Page, start: { has_key: boolean; last4: string | null } = { has_key: false, last4: null }) {
  const sent: Request[] = []
  let state = { ...start, added_at: start.has_key ? Date.now() / 1000 - 3600 : null, unreadable: false }
  await page.route('**/api/auth/ollama-key', (route) => {
    const r = route.request()
    if (r.method() !== 'GET') sent.push(r)
    if (r.method() === 'POST') {
      const key = (r.postDataJSON() as { key: string }).key
      state = { has_key: true, last4: key.slice(-4), added_at: Date.now() / 1000, unreadable: false }
    } else if (r.method() === 'DELETE') {
      state = { has_key: false, last4: null, added_at: null, unreadable: false }
    }
    return route.fulfill({ json: state, headers: { 'cache-control': 'no-store' } })
  })
  return sent
}

test('saves a key, shows only its last four characters, then removes it after a confirm', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'baihe_csrf', value: 'csrf-key', url: baseURL! }])
  const s = await mockDevices(page, member)
  const sent = await mockKey(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  const card = page.getByRole('region', { name: TITLE })
  await expect(card).toContainText('No key saved')

  await card.getByRole('button', { name: 'Save key' }).click()
  await expect(card).toContainText('Paste your key first.')
  expect(sent).toHaveLength(0)
  const input = card.getByLabel('Ollama API key')
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill(KEY)
  await card.getByRole('button', { name: 'Save key' }).click()
  await expect(card).toContainText('Key ending in UvWx')
  await expect(card).toContainText('Saved. Your next cloud-model run uses your account.')
  await expect(card.getByLabel('Replace with a new key')).toHaveValue('')
  await expect(page.locator('body')).not.toContainText(KEY)
  expect(sent[0].postDataJSON()).toEqual({ key: KEY })
  expect(sent[0].headers()['x-csrf-token']).toBe('csrf-key')

  await card.getByRole('button', { name: 'Remove your Ollama key' }).click()
  expect(sent).toHaveLength(1) // the first press only arms
  await card.getByRole('button', { name: 'Confirm remove your Ollama key' }).click()
  await expect(card).toContainText('No key saved')
  expect(sent.map((r) => r.method())).toEqual(['POST', 'DELETE'])
  expect(s.unmocked).toEqual([])
})

test('is not offered to a member who cannot start jobs', async ({ page }) => {
  await mockDevices(page, ME.signedIn)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Account and display')
  await expect(page.getByRole('region', { name: 'Signed-in devices' })).toBeVisible()
  await expect(page.getByRole('region', { name: TITLE })).toHaveCount(0)
})

test('People: an admin sees who has their own key and can remove it, never read it', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: {
    auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'pc',
    permissions: ['library.read', 'admin.diagnostics', 'admin.users.read', 'admin.users'],
    user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_local_owner: false },
  } }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  const row = {
    id: 2, email: 'kid@example.com', display_name: 'Kid', is_admin: false, is_active: true,
    has_google_binding: true, created_at: '2026-09-29T10:00:00Z', permissions: ['jobs.start'],
    active_sessions: 1, is_self: false, has_own_ollama_key: true,
  }
  let users = [row]
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users } }))
  const sent: Request[] = []
  await page.route('**/api/admin/users/2/ollama-key', (r) => {
    sent.push(r.request())
    users = [{ ...row, has_own_ollama_key: false }]
    return r.fulfill({ json: { user_id: 2, removed: true } })
  })
  await page.goto('/#/admin')
  const summary = page.locator('summary', { hasText: /^People/ }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  const kid = page.getByRole('list', { name: 'People' }).locator('li').first()
  await expect(kid).toContainText('Own Ollama key')
  await kid.getByRole('button', { name: 'Remove Ollama key kid@example.com' }).click()
  expect(sent).toHaveLength(0)
  await kid.getByRole('button', { name: 'Confirm remove the Ollama key of kid@example.com' }).click()
  await expect(page.getByTestId('admin-users')).toContainText('kid@example.com: removed their Ollama key.')
  await expect(kid).not.toContainText('Own Ollama key')
  expect(sent.map((r) => `${r.method()} ${new URL(r.url()).pathname}`)).toEqual(['DELETE /api/admin/users/2/ollama-key'])
  expect(unmocked).toEqual([])
})
