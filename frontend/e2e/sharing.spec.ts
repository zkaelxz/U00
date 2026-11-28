import { expect, test, type Page, type Route } from '@playwright/test'

import { ME, type MeBody } from './authMocks'
import { openSettingsGroups } from './settingsNav'
import { guardWrites } from './mockHelpers'

// Settings > Sharing. /api/auth/me and /api/sharing/* are mocked with a small
// stateful fixture; a catch-all aborts (and records) any other non-GET /api
// call, so nothing is written to the seeded library.

type Item = {
  kind: 'series' | 'drama'
  id: number
  title: string
  owner_name: string
  owner_user_id: number | null
  created_at_pc: boolean
  is_private: boolean
  series_id: number | null
  series_name: string | null
  series_is_private: boolean | null
}

const ITEMS: Item[] = [
  { kind: 'drama', id: 3, title: 'Hidden Letters', owner_name: 'Ann', owner_user_id: 2, created_at_pc: false, is_private: true, series_id: null, series_name: null, series_is_private: null },
  { kind: 'series', id: 9, title: 'Saga', owner_name: 'Ann', owner_user_id: 2, created_at_pc: false, is_private: false, series_id: null, series_name: null, series_is_private: null },
  { kind: 'drama', id: 4, title: 'Saga Ep 1', owner_name: 'Bo', owner_user_id: 3, created_at_pc: false, is_private: false, series_id: 9, series_name: 'Saga', series_is_private: false },
  { kind: 'drama', id: 5, title: 'Solo Story', owner_name: 'PC owner', owner_user_id: null, created_at_pc: true, is_private: true, series_id: null, series_name: null, series_is_private: null },
]

const CONFLICT = "Move other people's titles out of this series first."
const ADMIN_ME: MeBody = { ...ME.authOff, permissions: ['admin.settings', 'admin.library', 'admin.users.read', 'admin.users', 'library.read', 'lines.edit'] }

async function mockSharing(page: Page, me: MeBody) {
  const state = { share: false, items: ITEMS.map((i) => ({ ...i })) }
  const posts: { path: string; body: unknown }[] = []
  const listCalls: string[] = []
  const unmocked = await guardWrites(page)
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me }))
  // Settings also shows a signed-in person their devices (signed-in-devices.spec.ts covers it).
  await page.route('**/api/auth/sessions', (route) =>
    route.fulfill({ json: { sessions: [], idle_timeout_days: 14, absolute_timeout_days: 30 } }))
  // ...and their browser-extension devices (extension-devices.spec.ts covers it).
  await page.route('**/api/auth/device-tokens', (route) => route.fulfill({ json: { tokens: [], max_active: 10 } }))
  await page.route('**/api/admin/users', (route) => route.fulfill({ json: { users: [
    { id: 2, email: 'ann@example.com', display_name: 'Ann', is_admin: false, is_active: true, has_google_binding: true, permissions: [], active_sessions: 0, is_self: false },
    { id: 3, email: 'bo@example.com', display_name: 'Bo', is_admin: false, is_active: true, has_google_binding: true, permissions: [], active_sessions: 0, is_self: false },
  ], member_permissions: [] } }))
  await page.route('**/api/sharing/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (path === '/api/sharing/share-by-default') {
      if (r.method() === 'POST') {
        const body = r.postDataJSON() as { share_by_default: boolean }
        posts.push({ path, body })
        state.share = body.share_by_default
      }
      return route.fulfill({ json: { share_by_default: state.share } })
    }
    if (path === '/api/sharing/items') {
      listCalls.push(r.url())
      return route.fulfill({ json: { total: state.items.length, offset: 0, limit: 100, items: state.items } })
    }
    const owner = path.match(/^\/api\/sharing\/(dramas|series)\/(\d+)\/owner$/)
    if (owner && r.method() === 'POST') {
      const body = r.postDataJSON() as { owner_user_id: number | null }
      posts.push({ path, body })
      const name = body.owner_user_id === null ? 'PC owner' : body.owner_user_id === 3 ? 'Bo' : 'Ann'
      return route.fulfill({ json: { kind: owner[1] === 'series' ? 'series' : 'drama', id: Number(owner[2]), owner_user_id: body.owner_user_id, owner_name: name } })
    }
    const m = path.match(/^\/api\/sharing\/(dramas|series)\/(\d+)\/private$/)
    if (m && r.method() === 'POST') {
      const body = r.postDataJSON() as { private: boolean }
      posts.push({ path, body })
      if (m[1] === 'series') {
        return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: CONFLICT } } })
      }
      return route.fulfill({ json: { kind: 'drama', id: Number(m[2]), is_private: body.private } })
    }
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return { posts, listCalls, unmocked }
}

test('admin: share-new-items switch, every item with its owner on the Sharing page, flips and a plain 409', async ({ page }) => {
  const { posts, unmocked } = await mockSharing(page, ADMIN_ME)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  let card = page.getByRole('region', { name: 'Sharing' })

  const shareDefault = card.getByRole('switch', { name: 'New items I create are shared with the household' })
  await expect(shareDefault).toHaveAttribute('aria-checked', 'false')
  await expect(card.getByTestId('share-default-help')).toHaveText(/^Off: .*private.*only affects new items/)
  await shareDefault.click()
  await expect(shareDefault).toHaveAttribute('aria-checked', 'true')
  await expect(card.getByTestId('share-default-help')).toHaveText(/^On: /)

  // The list is a page of its own, linked from the card.
  await card.getByRole('link', { name: 'Open the Sharing page' }).click()
  await expect(page).toHaveURL(/#\/sharing$/)
  card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '#/settings')
  const list = card.getByRole('group', { name: 'Titles and series' })
  await expect(list.getByRole('listitem')).toHaveCount(4)
  // Grouped by who can see them: Saga and its episode are shared, the other two private.
  await expect(list.getByRole('heading')).toHaveText(['Shared (2)', 'Private (2)'])
  await expect(card.getByRole('list', { name: 'Private titles and series' }).getByRole('listitem')).toHaveCount(2)
  await expect(list).not.toContainText('@')
  const hidden = card.getByTestId('sharing-drama:3')
  await expect(hidden).toContainText('Owner: Ann')
  await expect(hidden).toContainText('Private')

  // A drama in a series has no switch of its own.
  const ep = card.getByTestId('sharing-drama:4')
  await expect(ep.getByRole('switch')).toHaveCount(0)
  await expect(ep).toContainText('Titles in a series follow the series')

  await card.getByRole('switch', { name: 'Share title “Hidden Letters” with the household' }).click()
  await expect(hidden).toContainText('Shared')
  await expect(card.getByRole('list', { name: 'Shared titles and series' }).getByTestId('sharing-drama:3')).toBeVisible()

  const series = card.getByRole('switch', { name: 'Share series “Saga” with the household' })
  await expect(series).toHaveAttribute('aria-checked', 'true')
  await series.click()
  await expect(card.getByTestId('sharing-series:9').getByRole('alert')).toHaveText(CONFLICT)
  await expect(series).toHaveAttribute('aria-checked', 'true')

  // An admin gives a title to someone else, or to the PC.
  const ownerPick = card.getByRole('combobox', { name: 'Owner of title “Hidden Letters”' })
  await expect(ownerPick).toHaveValue('2')
  await ownerPick.selectOption({ label: 'Bo' })
  await expect(hidden).toContainText('Owner: Bo')
  await expect(list).not.toContainText('@')

  // Items made at the PC (no owner) are private until an admin shares them.
  await expect(card.getByTestId('sharing-pc-note')).toHaveText(/^Items made at the PC .* belong to the PC owner and start private\. .*sign-in is turned on, others will not see them/)
  await expect(card.getByTestId('sharing-drama:5')).toContainText('Created at the PC')
  await expect(hidden).not.toContainText('Created at the PC')
  await card.getByRole('combobox', { name: 'Show' }).selectOption({ label: 'Private, created at the PC' })
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await card.getByRole('switch', { name: 'Share title “Solo Story” with the household' }).click()
  await expect(list).toHaveCount(0)
  await expect(card.getByText('Nothing matches. Change Show or Owner to see more.')).toBeVisible()

  // Ordered by owner, then narrowed to one owner.
  await card.getByRole('combobox', { name: 'Show' }).selectOption({ label: 'Everything' })
  await card.getByRole('combobox', { name: 'Order' }).selectOption({ label: 'Owner' })
  await expect(list.getByRole('heading')).toHaveText(['Ann (1)', 'Bo (2)', 'PC owner (1)'])
  await card.getByRole('combobox', { name: 'Owner', exact: true }).selectOption({ label: 'Bo' })
  await expect(list.getByRole('heading')).toHaveText(['Bo (2)'])
  await expect(list.getByRole('listitem')).toHaveCount(2)
  await card.getByRole('combobox', { name: 'Order' }).selectOption({ label: 'Title' })
  await expect(list.getByRole('listitem')).toHaveText([/^Hidden Letters/, /^Saga Ep 1/])
  // Giving an owner's last item away drops the filter back to everyone instead of an empty list.
  await card.getByRole('combobox', { name: 'Owner', exact: true }).selectOption({ label: 'Ann' })
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await card.getByRole('combobox', { name: 'Owner of series “Saga”' }).selectOption({ label: 'Bo' })
  await expect(list.getByRole('listitem')).toHaveCount(4)
  await expect(card.getByRole('combobox', { name: 'Owner', exact: true })).toHaveValue('')

  expect(posts).toEqual([
    { path: '/api/sharing/share-by-default', body: { share_by_default: true } },
    { path: '/api/sharing/dramas/3/private', body: { private: false } },
    { path: '/api/sharing/series/9/private', body: { private: true } },
    { path: '/api/sharing/dramas/3/owner', body: { owner_user_id: 3 } },
    { path: '/api/sharing/dramas/5/private', body: { private: false } },
    { path: '/api/sharing/series/9/owner', body: { owner_user_id: 3 } },
  ])
  expect(unmocked).toEqual([])
})

test('household member: only their own share-new-items switch, no item list, fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const { listCalls, unmocked } = await mockSharing(page, ME.signedIn)
  await page.route('**/api/settings', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  const card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByRole('switch', { name: 'New items I create are shared with the household' })).toHaveAttribute(
    'aria-checked',
    'false',
  )
  await expect(card.getByTestId('share-default-help')).toContainText('An admin can change existing ones one at a time')
  await expect(card.getByRole('list', { name: 'Titles and series' })).toHaveCount(0)
  await expect(card.getByTestId('sharing-pc-note')).toHaveCount(0)
  // Not an admin: the settings 403 hides the admin cards without an error banner.
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(listCalls).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const box = await card.getByRole('switch').boundingBox()
  expect(box && box.height).toBeGreaterThanOrEqual(24)
  expect(unmocked).toEqual([])
})

test('admin at the PC with nobody added: no Owner pickers, the switches stay', async ({ page }) => {
  await mockSharing(page, ADMIN_ME)
  const people = page.waitForResponse('**/api/admin/users')
  await page.route('**/api/admin/users', (route) => route.fulfill({ json: { users: [], member_permissions: [] } }))
  await page.route('**/api/sharing/items*', (route) => route.fulfill({ json: { total: 1, offset: 0, limit: 100, items: [ITEMS[3]] } }))
  await page.goto('/#/sharing')
  await people
  const card = page.getByRole('region', { name: 'Sharing' })
  const row = card.getByTestId('sharing-drama:5')
  await expect(row).toContainText('Owner: PC owner')
  await expect(row.getByRole('switch', { name: 'Share title “Solo Story” with the household' })).toBeVisible()
  await expect(card.getByRole('combobox', { name: /^Owner of/ })).toHaveCount(0)
})

test('admin: with no titles or series the list offers the way to the Library', async ({ page }) => {
  await mockSharing(page, ADMIN_ME)
  await page.route('**/api/sharing/items*', (route) => route.fulfill({ json: { total: 0, offset: 0, limit: 100, items: [] } }))
  await page.goto('/#/sharing')
  const card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByText('There are no titles or series yet.')).toBeVisible()
  await expect(card.getByRole('link', { name: 'Go to Library' })).toHaveAttribute('href', '#/library')
})

test('/me unavailable: no controls or item list, only a retry; Retry brings the card back', async ({ page }) => {
  const { listCalls, unmocked } = await mockSharing(page, ADMIN_ME)
  // The app also retries /me on its own timer. In 'hold' those requests are parked instead of answered, so
  // nothing can bring the card back before the Retry click and the button cannot vanish under it.
  let mode: 'down' | 'hold' | 'up' = 'down'
  const parked: Route[] = []
  await page.route('**/api/auth/me', (route) => {
    if (mode === 'hold') parked.push(route)
    else if (mode === 'down') void route.fulfill({ status: 502, contentType: 'text/html', body: '<h1>Bad gateway</h1>' })
    else void route.fallback()
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'My account and look')
  const card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByText("Couldn't check your account yet")).toBeVisible()
  await expect(card.getByRole('switch')).toHaveCount(0)
  await expect(card.getByTestId('sharing-pc-note')).toHaveCount(0)
  expect(listCalls).toEqual([])
  mode = 'hold'
  const before = parked.length
  await card.getByRole('button', { name: 'Retry' }).click()
  await expect.poll(() => parked.length).toBeGreaterThan(before)
  mode = 'up'
  await Promise.all(parked.splice(0).map((route) => route.fallback()))
  await expect(card.getByRole('switch', { name: 'New items I create are shared with the household' })).toBeVisible()
  expect(unmocked).toEqual([])
})

test('admin: 300 items load in pages of 200, sort by owner, and scroll inside their box', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const { listCalls } = await mockSharing(page, ADMIN_ME)
  const many: Item[] = Array.from({ length: 300 }, (_, n) => ({
    kind: 'drama', id: 100 + n, title: `Title ${n + 1}`, owner_name: n % 3 ? 'Ann' : 'Bo', owner_user_id: n % 3 ? 2 : 3,
    created_at_pc: false, is_private: n % 2 === 0, series_id: null, series_name: null, series_is_private: null,
  }))
  await page.route('**/api/sharing/items*', (route) => {
    const q = new URL(route.request().url()).searchParams
    listCalls.push(route.request().url())
    const offset = Number(q.get('offset')), limit = Number(q.get('limit'))
    return route.fulfill({ json: { total: many.length, offset, limit, items: many.slice(offset, offset + limit) } })
  })
  await page.goto('/#/sharing')
  const card = page.getByRole('region', { name: 'Sharing' })
  const list = card.getByRole('group', { name: 'Titles and series' })
  await expect(list.getByRole('heading')).toHaveText(['Shared (150)', 'Private (150)'])
  expect(listCalls.map((u) => new URL(u).search)).toEqual(['?offset=0&limit=200', '?offset=200&limit=200'])
  await card.getByRole('combobox', { name: 'Order' }).selectOption({ label: 'Owner' })
  await expect(list.getByRole('heading')).toHaveText(['Ann (200)', 'Bo (100)'])
  // The list scrolls in its box, so the page stays about a screen tall however long the library is.
  const box = (await list.boundingBox())!
  expect(box.height).toBeLessThanOrEqual(0.7 * 800 + 2)
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(1400)
})

test('household member: the Sharing page asks for no list and points back to Settings', async ({ page }) => {
  const { listCalls, unmocked } = await mockSharing(page, ME.signedIn)
  await page.goto('/#/sharing')
  const card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByText('Only an admin can see and change every item.')).toBeVisible()
  await expect(card.getByRole('link', { name: 'Go to Settings' })).toHaveAttribute('href', '#/settings')
  await expect(card.getByRole('switch')).toHaveCount(0)
  expect(listCalls).toEqual([])
  expect(unmocked).toEqual([])
})
