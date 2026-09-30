import { expect, test, type Page, type Request } from '@playwright/test'

// Diagnostics > Users and Audit log (desktop). Every /api/admin call is
// mocked; a catch-all fails the test on any other non-GET /api call, so no
// real account is ever changed.

type U = {
  id: number; email: string; display_name: string; is_admin: boolean; is_active: boolean
  has_google_binding: boolean; created_at: string | null; permissions: string[]
  active_sessions: number; is_self: boolean
}

const user = (o: Partial<U>): U => ({
  id: 1, email: 'x@example.com', display_name: '', is_admin: false, is_active: true,
  has_google_binding: true, created_at: '2026-09-29T10:00:00Z', permissions: ['library.read'],
  active_sessions: 1, is_self: false, ...o,
})

const ME_ADMIN = user({ id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_self: true })
const KID = user({ id: 2, email: 'kid@example.com', display_name: 'Kid', active_sessions: 2 })
const GUEST = user({ id: 3, email: 'guest@example.com', is_active: false, active_sessions: 0, has_google_binding: false })

const event = (id: number, o: Record<string, unknown> = {}) => ({
  id, ts: `2026-09-30T12:${String(id).padStart(2, '0')}:00Z`, user_id: 2, action: 'login.success',
  detail: `user 2 ip 203.0.113`, ...o,
})

const me = (permissions: string[]) => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions,
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: permissions.includes('admin.users'), is_local_owner: false },
})

/** Catch-all first (Playwright tries the newest route first, so later mocks win). */
async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

async function mockPage(page: Page, permissions: string[]) {
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: me(permissions) }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
}

const openSection = (page: Page, title: RegExp) => page.locator('summary', { hasText: title }).first().click()

test('Users: lists accounts, explains the guards, and deactivates after a confirm', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.diagnostics', 'admin.users'])
  let users = [ME_ADMIN, KID, GUEST]
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users } }))
  const sent: Request[] = []
  await page.route('**/api/admin/users/*/*', (r) => {
    sent.push(r.request())
    const updated = { ...KID, is_active: false, active_sessions: 0 }
    users = [ME_ADMIN, updated, GUEST]
    return r.fulfill({ json: updated })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Users/)
  const list = page.getByRole('list', { name: 'Users' })
  await expect(list.locator('li')).toHaveCount(3)

  const self = list.locator('li').nth(0)
  await expect(self).toContainText('Owner (owner@example.com)')
  await expect(self).toContainText('You')
  await expect(self).toContainText("You can't deactivate your own account.")
  await expect(self.getByRole('button', { name: 'Deactivate owner@example.com' })).toBeDisabled()
  await expect(self.getByRole('button', { name: 'Sign out everywhere owner@example.com' })).toBeDisabled()
  await expect(list.locator('li').nth(2).getByRole('button', { name: 'Activate guest@example.com' })).toBeEnabled()

  const kid = list.locator('li').nth(1)
  await expect(kid).toContainText('signed in on 2 devices')
  await kid.getByRole('button', { name: 'Deactivate kid@example.com' }).click()
  expect(sent).toHaveLength(0) // the first press only arms
  await kid.getByRole('button', { name: 'Confirm deactivate kid@example.com' }).click()
  await expect(page.getByTestId('admin-users')).toContainText('kid@example.com is deactivated and signed out.')
  expect(sent.map((r) => `${r.method()} ${new URL(r.url()).pathname}`)).toEqual(['POST /api/admin/users/2/deactivate'])
  await expect(kid).toContainText('Deactivated')
  await expect(kid.getByRole('button', { name: 'Activate kid@example.com' })).toBeVisible()
  expect(unmocked).toEqual([])
})

test('Users: a refused action shows the server reason in plain words', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users'])
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, other, KID] } }))
  await page.route('**/api/admin/users/2/revoke-sessions', (r) => r.fulfill({
    status: 404, json: { error: { code: 'not_found', message: 'User not found.' } },
  }))
  await page.route('**/api/admin/users/4/deactivate', (r) => r.fulfill({
    status: 409,
    json: { error: { code: 'conflict', message: "This is the last active admin. Baihe needs at least one, so it can't be deactivated." } },
  }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Users/)
  const section = page.getByTestId('admin-users')

  await section.getByRole('button', { name: 'Deactivate second@example.com' }).click()
  await section.getByRole('button', { name: 'Confirm deactivate second@example.com' }).click()
  const alert = section.getByRole('alert')
  await expect(alert).toContainText('That cannot be done right now')
  await expect(alert).toContainText('This is the last active admin.')

  await section.getByRole('button', { name: 'Sign out everywhere kid@example.com' }).click()
  await section.getByRole('button', { name: 'Confirm sign out kid@example.com' }).click()
  await expect(alert).toContainText('That item could not be found.')
  await expect(alert).toContainText('User not found.')
  expect(unmocked).toEqual([])
})

test('Audit log: newest first, filters by action and user, pages back', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users'])
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, KID] } }))
  const asked: URLSearchParams[] = []
  await page.route((u) => u.pathname === '/api/admin/audit', (r) => {
    const q = new URL(r.request().url()).searchParams
    asked.push(q)
    if (q.get('before_id') === '8') {
      return r.fulfill({ json: { events: [event(7, { user_id: null, action: 'login.denied', detail: 'not_allowlisted ip 198.51.100' })], next_before_id: null, actions: ['login.denied', 'login.success', 'user.deactivate'] } })
    }
    if (q.get('action') === 'user.deactivate') {
      return r.fulfill({ json: { events: [event(5, { user_id: 1, action: 'user.deactivate', detail: 'user 2' })], next_before_id: null, actions: ['login.denied', 'login.success', 'user.deactivate'] } })
    }
    return r.fulfill({ json: { events: [event(9), event(8)], next_before_id: 8, actions: ['login.denied', 'login.success', 'user.deactivate'] } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Audit log/)
  const rows = page.getByRole('table', { name: 'Audit log' }).locator('tbody tr')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toContainText('2026-09-30 12:09 UTC')
  await expect(rows.first()).toContainText('Kid (kid@example.com)')
  await expect(rows.first()).toContainText('Signed in')
  expect(asked[0].get('limit')).toBe('50')

  await page.getByRole('button', { name: 'Show older' }).click()
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(2)).toContainText('PC or system')
  await expect(rows.nth(2)).toContainText('Sign-in refused')
  await expect(page.getByRole('button', { name: 'Show older' })).toHaveCount(0)

  await page.getByRole('combobox', { name: /^Action/ }).selectOption('user.deactivate')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('User deactivated')
  await expect(rows.first()).toContainText('Owner (owner@example.com)')
  await page.getByRole('combobox', { name: /^User/ }).selectOption('2')
  await expect.poll(() => asked.at(-1)?.get('user_id')).toBe('2')
  expect(asked.at(-1)?.get('action')).toBe('user.deactivate')
  // Read-only: nothing in the section edits or deletes a row.
  await expect(page.getByTestId('audit-log').getByRole('button', { name: /delete|edit|clear/i })).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('hidden from a signed-in user without admin.users', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.diagnostics'])
  const admin: string[] = []
  await page.route('**/api/admin/**', (r) => {
    admin.push(r.request().url())
    return r.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
  })
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('diagnostics-summary')).toBeVisible()
  await expect(page.locator('summary', { hasText: /^Users/ })).toHaveCount(0)
  await expect(page.locator('summary', { hasText: /^Audit log/ })).toHaveCount(0)
  expect(admin).toEqual([])
  expect(unmocked).toEqual([])
})
