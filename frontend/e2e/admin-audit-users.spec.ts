import { expect, test, type Page, type Request } from '@playwright/test'
import { navLink } from './settingsNav'
import { guardWrites } from './mockHelpers'

// People and access > People and Activity log (desktop). Every /api/admin call is
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
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: permissions.includes('admin.users.read'), is_local_owner: false },
})

/** Catch-all first (Playwright tries the newest route first, so later mocks win). */
async function guard(page: Page): Promise<string[]> {
  const unmocked = await guardWrites(page)
  return unmocked
}

async function mockPage(page: Page, permissions: string[], local = true) {
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: me(permissions) }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
}

const openPane = async (page: Page, name: string) => {
  // The People tab's name ends with its count.
  const tab = page.getByRole('tab', { name: new RegExp(`^${name}`) })
  if ((await tab.getAttribute('aria-selected')) !== 'true') await tab.click()
}

/** A person is one row that opens on click; its details and buttons are inside. */
const openRows = async (page: Page) => {
  const list = page.getByRole('list', { name: 'People' })
  await expect(list.locator(':scope > li').first()).toBeVisible()
  const closed = list.locator(':scope > li > details:not([open]) > summary')
  while (await closed.count()) await closed.first().click()
  return list
}

test('People: lists accounts, explains the guards, and deactivates after a confirm', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.diagnostics', 'admin.users.read', 'admin.users'])
  let users = [ME_ADMIN, KID, GUEST]
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users } }))
  const sent: Request[] = []
  await page.route('**/api/admin/users/*/*', (r) => {
    sent.push(r.request())
    const updated = { ...KID, is_active: false, active_sessions: 0 }
    users = [ME_ADMIN, updated, GUEST]
    return r.fulfill({ json: updated })
  })
  await page.goto('/#/admin')
  await openPane(page, 'People')
  const list = await openRows(page)
  await expect(list.locator('li')).toHaveCount(3)

  const self = list.locator('li').nth(0)
  await expect(self).toContainText('Owner · owner@example.com')
  await expect(self).toContainText('You')
  await expect(self).toContainText("You can't deactivate your own account.")
  await expect(self.getByRole('button', { name: 'Deactivate owner@example.com' })).toBeDisabled()
  await expect(self.getByRole('button', { name: 'Sign out everywhere owner@example.com' })).toBeDisabled()
  await expect(list.locator('li').nth(2).getByRole('button', { name: 'Activate guest@example.com' })).toBeEnabled()

  const kid = list.locator('li').nth(1)
  await expect(kid).toContainText(/signed in on 2 devices/i)
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
  await mockPage(page, ['admin.users.read', 'admin.users'])
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, other, KID] } }))
  await page.route('**/api/admin/users/2/revoke-sessions', (r) => r.fulfill({
    status: 404, json: { error: { code: 'not_found', message: 'User not found.' } },
  }))
  await page.route('**/api/admin/users/4/deactivate', (r) => r.fulfill({
    status: 409,
    json: { error: { code: 'conflict', message: "This is the last active admin. Baihe needs at least one, so it can't be deactivated." } },
  }))
  await page.goto('/#/admin')
  await openPane(page, 'People')
  await openRows(page)
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

test('Users: away from the PC, admin accounts are off with the reason; others still work', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'], false)
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  const otherOff = user({ id: 5, email: 'third@example.com', is_admin: true, is_active: false, active_sessions: 0 })
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, other, otherOff, KID] } }))
  await page.goto('/#/admin')
  await openPane(page, 'People')
  const list = await openRows(page)
  const second = list.locator('li').nth(1)
  await expect(second).toContainText('Admin accounts can only be changed on the main PC.')
  await expect(second.getByRole('button', { name: 'Deactivate second@example.com' })).toBeDisabled()
  await expect(second.getByRole('button', { name: 'Sign out everywhere second@example.com' })).toBeDisabled()
  await expect(list.locator('li').nth(2).getByRole('button', { name: 'Activate third@example.com' })).toBeDisabled()
  await expect(list.getByRole('button', { name: /^Remove admin/ })).toHaveCount(0)
  const kid = list.locator('li').nth(3)
  await expect(kid.getByRole('button', { name: 'Deactivate kid@example.com' })).toBeEnabled()
  await expect(kid.getByRole('button', { name: 'Sign out everywhere kid@example.com' })).toBeEnabled()
  expect(unmocked).toEqual([])
})

test('Users: on the PC, removes admin rights after a confirm and refreshes the list', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  let users = [ME_ADMIN, other, KID]
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users } }))
  const sent: string[] = []
  await page.route('**/api/admin/users/4/revoke-admin', (r) => {
    sent.push(`${r.request().method()} ${new URL(r.request().url()).pathname}`)
    const updated = { ...other, is_admin: false, active_sessions: 0 }
    users = [ME_ADMIN, updated, KID]
    return r.fulfill({ json: updated })
  })
  await page.goto('/#/admin')
  await openPane(page, 'People')
  const list = await openRows(page)
  const self = list.locator('li').nth(0)
  await expect(self).toContainText("You can't remove your own admin rights.")
  await expect(self.getByRole('button', { name: 'Remove admin owner@example.com' })).toBeDisabled()
  await expect(list.locator('li').nth(2).getByRole('button', { name: /^Remove admin/ })).toHaveCount(0)

  const second = list.locator('li').nth(1)
  await second.getByRole('button', { name: 'Remove admin second@example.com' }).click()
  expect(sent).toHaveLength(0) // the first press only arms
  await second.getByRole('button', { name: 'Confirm remove admin rights from second@example.com' }).click()
  await expect(page.getByTestId('admin-users')).toContainText('second@example.com is no longer an admin and was signed out.')
  expect(sent).toEqual(['POST /api/admin/users/4/revoke-admin'])
  await expect(second).not.toContainText('Admin')
  await expect(list.getByRole('button', { name: /^Remove admin/ })).toHaveCount(1) // only the caller's, still off
  await expect(self).toContainText("You can't remove your own admin rights.")
  expect(unmocked).toEqual([])
})

test('Users: a last-admin refusal from the server is shown in the banner', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, other] } }))
  await page.route('**/api/admin/users/4/revoke-admin', (r) => r.fulfill({
    status: 409,
    json: { error: { code: 'conflict', message: "This is the last active admin. Baihe needs at least one, so their admin rights can't be removed." } },
  }))
  await page.goto('/#/admin')
  await openPane(page, 'People')
  await openRows(page)
  const section = page.getByTestId('admin-users')
  await section.getByRole('button', { name: 'Remove admin second@example.com' }).click()
  await section.getByRole('button', { name: 'Confirm remove admin rights from second@example.com' }).click()
  await expect(section.getByRole('alert')).toContainText("This is the last active admin. Baihe needs at least one, so their admin rights can't be removed.")
  expect(unmocked).toEqual([])
})

test('Users: an admin on the household address sees the list without any buttons', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.users.read'], false)
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, KID, GUEST] } }))
  await page.goto('/#/admin')
  await openPane(page, 'People')
  await openRows(page)
  const section = page.getByTestId('admin-users')
  await expect(section.getByRole('list', { name: 'People' }).locator('li')).toHaveCount(3)
  await expect(section).toContainText('Account changes are made on the main PC.')
  await expect(section.getByRole('button')).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('Activity log: newest first, filters by action and user, pages back', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
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
  await page.goto('/#/admin')
  await openPane(page, 'Activity')
  const rows = page.getByRole('table', { name: 'Activity log' }).locator('tbody tr')
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

test('hidden from a signed-in user without admin.users.read', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.diagnostics'])
  const admin: string[] = []
  await page.route('**/api/admin/**', (r) => {
    admin.push(r.request().url())
    return r.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
  })
  await page.goto('/#/admin')
  await expect(page.getByText('Only an admin can see this page.')).toBeVisible()
  await expect(navLink(page, 'People and access')).toHaveCount(0)
  await expect(page.getByRole('tab')).toHaveCount(0)
  expect(admin).toEqual([])
  expect(unmocked).toEqual([])
})

const REMOTE_SETUP = { steps: [], next: null, fields: { public_url: false, google_client_id: false, google_client_secret: false }, restart_needed: false }

test('People and access: a list down the left, People first, the pane remembered; links land on their part', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.diagnostics', 'admin.users.read', 'admin.users'])
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [] } }))
  await page.route('**/api/diagnostics/remote-health/ip-check', (r) => r.fulfill({ json: { configured: false } }))
  await page.route('**/api/diagnostics/remote-setup', (r) => r.fulfill({ json: REMOTE_SETUP }))
  await page.goto('/#/admin')
  await expect(page.getByRole('heading', { name: 'People and access', level: 1 })).toBeVisible()
  await expect(navLink(page, 'People and access')).toBeVisible()
  const list = page.getByRole('tablist', { name: 'People and access' })
  // PC profiles is only for the PC window with sign-in off, which this mocked sign-in is not.
  await expect(list.getByRole('tab')).toHaveText(['People0', 'Activity', 'Remote access'])
  await expect(list.getByRole('tab', { name: /^People/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('section[aria-label="People and access"]')).toHaveAttribute('data-page-shell', 'standard')
  // Empty text names no command.
  await expect(page.getByText('No one added yet. Use Add person to add someone by their Google email; then they can sign in with that Google account.')).toBeVisible()
  // The form opens from + Add person; its help says what a new person starts with (playing media is a default since 2026-11-17).
  await expect(page.getByRole('form', { name: 'Add a person' })).toHaveCount(0)
  await page.getByRole('button', { name: '+ Add person' }).click()
  const form = page.getByRole('form', { name: 'Add a person' })
  await form.getByRole('button', { name: 'Help: Add a person' }).focus()
  await expect(form.getByRole('tooltip')).toContainText('use Review, play audio and video)')
  await expect(page.locator('section[aria-label="People and access"]')).not.toContainText('grant-admin')

  await openPane(page, 'Remote access')
  for (const title of ['Set up remote access', 'Public address check', 'Uploads from away']) {
    await expect(page.getByRole('heading', { name: title, level: 2 })).toBeVisible()
  }
  await page.reload()
  await expect(list.getByRole('tab', { name: 'Remote access' })).toHaveAttribute('aria-selected', 'true')

  // ?card=remote is new; the older card links still land on their part of a pane, once: switching back later leaves focus alone.
  for (const [card, tab, heading] of [['people', /^People/, 'People'], ['remote', 'Remote access', 'Set up remote access'], ['address', 'Remote access', 'Public address check'], ['activity', 'Activity', 'Activity log']] as const) {
    await page.goto(`/#/admin?card=${card}`)
    await expect(list.getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('heading', { name: heading, level: 2 })).toBeFocused()
  }
  await openPane(page, 'People')
  await openPane(page, 'Activity')
  await expect(list.getByRole('tab', { name: 'Activity' })).toBeFocused()
  expect(unmocked).toEqual([])
})

for (const scheme of ['light', 'dark'] as const) {
  test(`${scheme}: People and access has no sideways scroll at 1280x800 with every row open`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.emulateMedia({ colorScheme: scheme })
    const unmocked = await guard(page)
    await mockPage(page, ['library.read', 'admin.diagnostics', 'admin.users.read', 'admin.users'])
    const many = Array.from({ length: 9 }, (_, n) => user({ id: 10 + n, email: `member-${n}-with-a-long-address@example.com` }))
    await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, KID, GUEST, ...many], member_permissions: RIGHTS } }))
    await page.route('**/api/admin/audit**', (r) => r.fulfill({ json: { events: [event(1)], next_before_id: null, actions: ['login.success'] } }))
    await page.route('**/api/diagnostics/remote-health/ip-check', (r) => r.fulfill({ json: { configured: true } }))
    await page.route('**/api/diagnostics/remote-setup', (r) => r.fulfill({ json: REMOTE_SETUP }))
    await page.goto('/#/admin')
    await openRows(page)
    // Ten members are wider than the pane: the grid scrolls sideways inside itself, never the page.
    await expect(page.getByTestId('admin-rights')).toBeVisible()
    const noSideScroll = async () => {
      const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
      expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    }
    await noSideScroll()
    for (const pane of ['Activity', 'Remote access']) {
      await openPane(page, pane)
      await noSideScroll()
    }
    await expect(page.getByRole('table', { name: 'Activity log' })).toBeHidden()
    expect(unmocked).toEqual([])
  })
}

const RIGHTS = ['library.read', 'lines.read', 'lines.edit', 'jobs.start', 'jobs.cancel', 'review.use',
  'media.import_url', 'sources.import', 'engines.paid', 'extension.send', 'media.stream']

test('People: the rights grid has a column per active member and gives and takes one right', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'], false)
  const other = user({ id: 4, email: 'second@example.com', is_admin: true })
  const sam = user({ id: 6, email: 'sam@example.com', display_name: 'Sam Sample', permissions: ['library.read', 'engines.paid'] })
  let kid = KID
  await page.route('**/api/admin/users', (r) => r.fulfill({
    json: { users: [ME_ADMIN, other, kid, GUEST, sam], member_permissions: RIGHTS } }))
  const sent: string[] = []
  await page.route('**/api/admin/users/2/permissions/*', (r) => {
    const method = r.request().method()
    sent.push(`${method} ${new URL(r.request().url()).pathname}`)
    const p = new URL(r.request().url()).pathname.split('/').pop()!
    kid = { ...kid, permissions: method === 'POST' ? [...kid.permissions, p] : kid.permissions.filter((x) => x !== p) }
    return r.fulfill({ json: kid })
  })
  await page.goto('/#/admin')
  const grid = page.getByRole('table', { name: 'Rights' })
  // Admins (the caller included) hold every right; a deactivated account holds none.
  await expect(grid.getByRole('columnheader')).toHaveText(['Right', 'Kid', 'Sam Sample'])
  await expect(grid.getByRole('switch')).toHaveCount(RIGHTS.length * 2)
  // Each switch names the right and the person, so a tap in the wrong column is announced.
  const lib = grid.getByRole('switch', { name: 'See the library for Kid (kid@example.com)' })
  const paid = grid.getByRole('switch', { name: 'Paid engines for Kid (kid@example.com)' })
  await expect(lib).toHaveAttribute('aria-checked', 'true')
  await expect(paid).toHaveAttribute('aria-checked', 'false')
  await expect(grid.getByRole('switch', { name: 'Paid engines for Sam Sample (sam@example.com)' })).toHaveAttribute('aria-checked', 'true')

  // Each right's "i" says what it allows.
  const help = grid.getByRole('button', { name: 'Help: Paid engines' })
  await help.focus()
  await expect(grid.getByRole('tooltip')).toContainText('Use engines that cost money')
  await help.blur()

  await paid.click()
  await expect(page.getByTestId('admin-users')).toContainText('kid@example.com: Paid engines given.')
  await expect(paid).toHaveAttribute('aria-checked', 'true')
  await lib.click()
  await expect(page.getByTestId('admin-users')).toContainText('kid@example.com: See the library taken away.')
  await expect(lib).toHaveAttribute('aria-checked', 'false')
  expect(sent).toEqual(['POST /api/admin/users/2/permissions/engines.paid',
    'DELETE /api/admin/users/2/permissions/library.read'])
  expect(unmocked).toEqual([])
})

test('People: name and limits save on Enter or leaving the field, show Saved only once the PC answers, and keep the text on a refusal', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
  let kid = { ...KID, monthly_paid_cap_usd: null as number | null, daily_job_limit: null as number | null }
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, kid], member_permissions: RIGHTS } }))
  const bodies: [string, unknown][] = []
  let release: () => void = () => undefined
  await page.route('**/api/admin/users/2/*', async (r) => {
    const what = new URL(r.request().url()).pathname.split('/').pop()!
    const body = r.request().postDataJSON() as Record<string, unknown>
    bodies.push([what, body])
    if (what === 'job-limit') {
      return r.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'The daily job limit is too high.' } } })
    }
    if (what === 'display-name') await new Promise<void>((done) => { release = done })
    kid = { ...kid, ...(what === 'display-name' ? { display_name: String(body.display_name) } : { monthly_paid_cap_usd: body.monthly_cap_usd as number | null }) }
    return r.fulfill({ json: kid })
  })
  await page.goto('/#/admin')
  const list = await openRows(page)
  const row = list.locator(':scope > li').nth(1)
  await expect(row.getByRole('button', { name: /^Save/ })).toHaveCount(0)

  const name = row.getByRole('textbox', { name: 'Name' })
  await name.fill('Kid Example')
  await name.press('Enter')
  await expect(row.getByText('Saving…')).toBeVisible()
  // Enter then leaving the field while it saves sends it once.
  await name.blur()
  // Nothing claims to be saved until the PC has answered.
  await expect(row.getByText('✓ Saved')).toHaveCount(0)
  release()
  await expect(row.getByText('✓ Saved')).toBeVisible()
  await expect(row.locator('summary')).toContainText('Kid Example')

  const paid = row.getByRole('textbox', { name: 'Paid limit a month (USD)' })
  await paid.fill('0')
  await paid.blur()
  await expect(row.getByText('Enter an amount above $0')).toBeVisible()
  await expect(paid).toHaveAttribute('aria-invalid', 'true')
  await paid.fill('12.50')
  await paid.blur()
  await expect(row.locator('summary')).toContainText('$12.50 a month')
  // Emptied and left, a limit stays: only Enter removes it.
  await paid.fill('')
  await paid.blur()
  await expect(row.getByText('Press Enter to remove the limit')).toBeVisible()
  await expect(row.locator('summary')).toContainText('$12.50 a month')
  await paid.press('Enter')
  await expect(row.locator('summary')).not.toContainText('a month')

  const jobs = row.getByRole('textbox', { name: 'Jobs a day' })
  await jobs.fill('9000')
  await jobs.press('Enter')
  await expect(row.getByText('The daily job limit is too high.')).toBeVisible()
  await expect(jobs).toHaveValue('9000')
  await expect(jobs).toHaveAttribute('aria-invalid', 'true')
  // Leaving the field doesn't send the refused value again; Enter does, as a retry.
  await jobs.blur()
  await jobs.press('Enter')
  await expect.poll(() => bodies.filter(([what]) => what === 'job-limit').length).toBe(2)
  // Esc puts the saved value back.
  await jobs.press('Escape')
  await expect(jobs).toHaveValue('')

  expect(bodies).toEqual([
    ['display-name', { display_name: 'Kid Example' }],
    ['paid-limit', { monthly_cap_usd: 12.5 }],
    ['paid-limit', { monthly_cap_usd: null }],
    ['job-limit', { jobs_per_day: 9000 }],
    ['job-limit', { jobs_per_day: 9000 }],
  ])
  expect(unmocked).toEqual([])
})

test('People: on the PC, adds a person; away from the PC there is no form', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
  const added = user({ id: 5, email: 'pc2@example.com', display_name: 'Second PC', has_google_binding: false,
    active_sessions: 0, permissions: ['library.read', 'extension.send'] })
  let users = [ME_ADMIN, KID]
  const bodies: unknown[] = []
  await page.route('**/api/admin/users', (r) => {
    if (r.request().method() === 'POST') {
      bodies.push(r.request().postDataJSON())
      users = [ME_ADMIN, KID, added]
      return r.fulfill({ json: added })
    }
    return r.fulfill({ json: { users, member_permissions: RIGHTS } })
  })
  await page.goto('/#/admin')
  await page.getByRole('button', { name: '+ Add person' }).click()
  const form = page.getByRole('form', { name: 'Add a person' })
  const submit = form.getByRole('button', { name: 'Add person' })
  await expect(submit).toBeDisabled()
  await form.getByLabel('Email').fill('PC2@example.com')
  await form.getByLabel('Name (optional)').fill('Second PC')
  await form.getByRole('switch', { name: 'Also allow the browser extension' }).click()
  await submit.click()
  await expect(page.getByTestId('admin-users')).toContainText('pc2@example.com was added.')
  await expect(page.getByRole('list', { name: 'People' }).locator(':scope > li')).toHaveCount(3)
  await expect(form).toHaveCount(0)
  expect(bodies).toEqual([{ email: 'PC2@example.com', display_name: 'Second PC', extension_send: true }])
  expect(unmocked).toEqual([])

  await mockPage(page, ['admin.users.read', 'admin.users'], false)
  await page.reload()
  await expect(page.getByRole('list', { name: 'People' }).locator(':scope > li')).toHaveCount(3)
  await expect(page.getByRole('button', { name: '+ Add person' })).toHaveCount(0)
  // Away from the PC: People and Activity only.
  await expect(page.getByRole('tablist', { name: 'People and access' }).getByRole('tab')).toHaveText(['People3', 'Activity'])
})

test('People: with 8 or more people a search narrows the list and the grid', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['admin.users.read', 'admin.users'])
  const many = Array.from({ length: 6 }, (_, n) => user({ id: 10 + n, email: `member${n}@example.com` }))
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN, KID, ...many], member_permissions: RIGHTS } }))
  await page.goto('/#/admin')
  const people = page.getByRole('list', { name: 'People' }).locator(':scope > li')
  await expect(people).toHaveCount(8)
  await page.getByRole('searchbox', { name: 'Find a person' }).fill('kid')
  await expect(people).toHaveCount(1)
  await expect(page.getByRole('table', { name: 'Rights' }).getByRole('columnheader')).toHaveText(['Right', 'Kid'])
  expect(unmocked).toEqual([])
})

test('People: without admin.users the rights are shown but cannot be changed', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, ['library.read', 'admin.users.read'], false)
  await page.route('**/api/admin/users', (r) => r.fulfill({
    json: { users: [ME_ADMIN, KID], member_permissions: RIGHTS } }))
  await page.goto('/#/admin')
  const grid = page.getByRole('table', { name: 'Rights' })
  await expect(grid.getByRole('switch', { name: 'See the library for Kid (kid@example.com)' })).toHaveAttribute('aria-checked', 'true')
  await expect(grid.getByRole('switch', { name: 'See the library for Kid (kid@example.com)' })).toBeDisabled()
  await expect(grid.getByRole('switch', { name: 'Paid engines for Kid (kid@example.com)' })).toBeDisabled()
  await expect(grid.getByRole('button', { name: 'Help: Paid engines' })).toBeEnabled()
  expect(unmocked).toEqual([])
})

test('PC profiles: shown at the PC window with sign-in off; the hide-all switch is sent with the PIN, not on its own', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/meta', (r) => r.fulfill({ json: {
    app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true, pc_profile: { pin_set: false, guest: false } } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: { ...me(['admin.users.read', 'admin.users']), auth_enabled: false } }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [ME_ADMIN] } }))
  const bodies: unknown[] = []
  await page.route('**/api/pc-profile/pin', (r) => {
    bodies.push(r.request().postDataJSON())
    return r.fulfill({ json: { pin_set: true, guest: false, hidden: 3 } })
  })
  await page.goto('/#/admin?card=pc-profiles')
  await expect(page.getByRole('tab', { name: 'PC profiles' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: /^People/ })).toBeVisible()
  const hideAll = page.getByRole('switch', { name: 'When profiles turn on, also mark all my current titles private' })
  await hideAll.click()
  await expect(hideAll).toHaveAttribute('aria-checked', 'true')
  expect(bodies).toEqual([])
  await page.getByLabel('PIN', { exact: true }).fill('123456')
  await page.getByRole('button', { name: 'Turn on profiles' }).click()
  await expect.poll(() => bodies).toEqual([{ new_pin: '123456', current_pin: null, hide_existing: true }])
  expect(unmocked).toEqual([])
})
