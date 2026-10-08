import { expect, test, type Page } from '@playwright/test'

import { ME, type MeBody } from './authMocks'
import { openSettingsGroups } from './settingsNav'

// Settings > Sharing. /api/auth/me and /api/sharing/* are mocked with a small
// stateful fixture; a catch-all aborts (and records) any other non-GET /api
// call, so nothing is written to the seeded library.

type Item = {
  kind: 'series' | 'drama'
  id: number
  title: string
  owner_name: string
  created_at_pc: boolean
  is_private: boolean
  series_id: number | null
  series_name: string | null
  series_is_private: boolean | null
}

const ITEMS: Item[] = [
  { kind: 'drama', id: 3, title: 'Hidden Letters', owner_name: 'Ann', created_at_pc: false, is_private: true, series_id: null, series_name: null, series_is_private: null },
  { kind: 'series', id: 9, title: 'Saga', owner_name: 'Ann', created_at_pc: false, is_private: false, series_id: null, series_name: null, series_is_private: null },
  { kind: 'drama', id: 4, title: 'Saga Ep 1', owner_name: 'Bo', created_at_pc: false, is_private: false, series_id: 9, series_name: 'Saga', series_is_private: false },
  { kind: 'drama', id: 5, title: 'Solo Story', owner_name: 'PC owner', created_at_pc: true, is_private: true, series_id: null, series_name: null, series_is_private: null },
]

const CONFLICT = "Move other people's dramas out of this series first."
const ADMIN_ME: MeBody = { ...ME.authOff, permissions: ['admin.settings', 'admin.library', 'library.read', 'lines.edit'] }

async function mockSharing(page: Page, me: MeBody) {
  const state = { share: false, items: ITEMS.map((i) => ({ ...i })) }
  const posts: { path: string; body: unknown }[] = []
  const listCalls: string[] = []
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me }))
  // Settings also shows a signed-in person their devices (signed-in-devices.spec.ts covers it).
  await page.route('**/api/auth/sessions', (route) =>
    route.fulfill({ json: { sessions: [], idle_timeout_days: 14, absolute_timeout_days: 30 } }))
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

test('admin: share-new-items switch, every item with its owner, flips and a plain 409', async ({ page }) => {
  const { posts, unmocked } = await mockSharing(page, ADMIN_ME)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Sharing' })

  const shareDefault = card.getByRole('switch', { name: 'New items I create are shared with the household' })
  await expect(shareDefault).toHaveAttribute('aria-checked', 'false')
  await expect(card.getByTestId('share-default-help')).toHaveText(/^Off: .*private.*only affects new items/)
  await shareDefault.click()
  await expect(shareDefault).toHaveAttribute('aria-checked', 'true')
  await expect(card.getByTestId('share-default-help')).toHaveText(/^On: /)

  const list = card.getByRole('list', { name: 'Dramas and series' })
  await expect(list.getByRole('listitem')).toHaveCount(4)
  await expect(list).not.toContainText('@')
  const hidden = card.getByTestId('sharing-drama:3')
  await expect(hidden).toContainText('Owner: Ann')
  await expect(hidden).toContainText('Private')

  // A drama in a series has no switch of its own.
  const ep = card.getByTestId('sharing-drama:4')
  await expect(ep.getByRole('switch')).toHaveCount(0)
  await expect(ep).toContainText('Dramas in a series follow the series')

  await card.getByRole('switch', { name: 'Share drama “Hidden Letters” with the household' }).click()
  await expect(hidden).toContainText('Shared')

  const series = card.getByRole('switch', { name: 'Share series “Saga” with the household' })
  await expect(series).toHaveAttribute('aria-checked', 'true')
  await series.click()
  await expect(card.getByTestId('sharing-series:9').getByRole('alert')).toHaveText(CONFLICT)
  await expect(series).toHaveAttribute('aria-checked', 'true')

  // Items made at the PC (no owner) are private until an admin shares them.
  await expect(card.getByTestId('sharing-pc-note')).toHaveText(/sign-in is turned on, others will not see them/)
  await expect(card.getByTestId('sharing-drama:5')).toContainText('Created at the PC')
  await expect(hidden).not.toContainText('Created at the PC')
  await card.getByRole('switch', { name: 'Show only private items created at the PC' }).click()
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await card.getByRole('switch', { name: 'Share drama “Solo Story” with the household' }).click()
  await expect(list).toHaveCount(0)
  await expect(card.getByText('No private items created at the PC.')).toBeVisible()

  expect(posts).toEqual([
    { path: '/api/sharing/share-by-default', body: { share_by_default: true } },
    { path: '/api/sharing/dramas/3/private', body: { private: false } },
    { path: '/api/sharing/series/9/private', body: { private: true } },
    { path: '/api/sharing/dramas/5/private', body: { private: false } },
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
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Sharing' })
  await expect(card.getByRole('switch', { name: 'New items I create are shared with the household' })).toHaveAttribute(
    'aria-checked',
    'false',
  )
  await expect(card.getByTestId('share-default-help')).toContainText('An admin can change existing ones one at a time')
  await expect(card.getByRole('list', { name: 'Dramas and series' })).toHaveCount(0)
  await expect(card.getByTestId('sharing-pc-note')).toHaveCount(0)
  // Not an admin: the settings 403 hides the admin cards without an error banner.
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(listCalls).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const box = await card.getByRole('switch').boundingBox()
  expect(box && box.height).toBeGreaterThanOrEqual(24)
  expect(unmocked).toEqual([])
})
