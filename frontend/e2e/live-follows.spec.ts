import { expect, test } from '@playwright/test'

import { SCREENS, SID, followRow, mockLive, openLive } from './liveMocks'

// The Live page's "Followed channels" card and the bell's Start Live action.
// Every /api call is mocked (liveMocks.ts); no channel is ever checked.
// Checks are manual by default; the hourly check is a per-channel toggle.

test('follow a channel, check it now and remove it', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Followed channels').click()
  await expect(live.getByText('No channels followed yet.')).toBeVisible()
  const follow = live.getByRole('button', { name: 'Follow', exact: true })
  await expect(follow).toBeDisabled()
  await expect(follow).toHaveAttribute('title', /Still needed/)
  await expect(live.getByRole('button', { name: 'Check all channels' })).toBeDisabled()
  await live.getByLabel('Channel', { exact: true }).fill('https://evil.example/@x')
  await expect(live.getByText('Only YouTube and Twitch channels can be followed.')).toBeVisible()
  await live.getByLabel('Channel', { exact: true }).fill('@somestreamer')
  await follow.click()
  const row = live.getByTestId('live-follow-row')
  await expect(row).toHaveCount(1)
  await expect(row.getByTestId('live-follow-status')).toContainText('Not checked yet')
  expect(m.follows.posts[0]).toEqual({ path: '/api/live/follows', body: { channel: '@somestreamer' } })
  // Manual by default: the hourly switch is off.
  await expect(row.getByRole('switch', { name: /every hour/ })).toHaveAttribute('aria-checked', 'false')
  await row.getByRole('button', { name: 'Check now' }).click()
  await expect(row.getByTestId('live-follow-status')).toHaveText('Checking…')
  await expect(row.getByRole('button', { name: 'Checking…' })).toBeDisabled()
  await expect(row.getByTestId('live-follow-status')).toContainText('Not live', { timeout: 10_000 })
  await expect(row.getByRole('button', { name: 'Check now' })).toHaveAttribute('title', /once a minute/)
  await page.screenshot({ path: `${SCREENS}/follows-desktop.png`, fullPage: true })
  await row.getByRole('button', { name: 'Remove' }).click()
  await row.getByRole('button', { name: 'Confirm remove' }).click()
  await expect(live.getByTestId('live-follow-row')).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('check all queues every channel and shows the results', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.can_auto_start = false
  m.follows.items = [followRow(1), followRow(2)]
  const live = await openLive(page)
  await live.getByText('Followed channels').click()
  await live.getByRole('button', { name: 'Check all my channels' }).click()
  await expect(live.getByText('Checking 2 channels…')).toBeVisible()
  await expect(live.getByTestId('live-follow-status').first()).toContainText('Not live', { timeout: 10_000 })
  await expect(live.getByTestId('live-follow-status').nth(1)).toContainText('Not live')
  expect(m.follows.posts.map((p) => p.path)).toEqual(['/api/live/follows/check-all'])
  expect(m.unmocked).toEqual([])
})

test('the automatic start needs the hourly check, and says so', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.items = [followRow(1, { state: 'upcoming', last_status: 'Scheduled', starts_at: 1_900_000_000, stream_title: 'Evening stream', last_checked_at: Date.now() / 1000 })]
  const live = await openLive(page)
  await live.getByText('Followed channels').click()
  const row = live.getByTestId('live-follow-row')
  await expect(row.getByTestId('live-follow-status')).toContainText(/Starts at \d.*: Evening stream/)
  const mode = row.getByRole('combobox', { name: /goes live/ })
  await expect(mode.locator('option[value=auto]')).toBeDisabled()
  await expect(row.getByText('Start Live automatically needs Check every hour on.')).toBeVisible()
  await row.getByRole('switch', { name: /every hour/ }).click()
  await expect.poll(() => m.follows.posts.length).toBe(1)
  expect(m.follows.posts[0]).toEqual({ path: '/api/live/follows/1', body: { auto_check: true } })
  await expect(mode.locator('option[value=auto]')).toBeEnabled()
  await expect(row.getByText('Start Live automatically needs Check every hour on.')).toHaveCount(0)
  await mode.selectOption('auto')
  await expect.poll(() => m.follows.posts.length).toBe(2)
  expect(m.follows.posts[1].body).toMatchObject({ mode: 'auto', options: { source_language: 'zh', max_minutes: 60 } })
  expect(m.unmocked).toEqual([])
})

test('a member sees no automatic-start choice', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.can_auto_start = false
  m.follows.items = [followRow(1)]
  const live = await openLive(page)
  await live.getByText('Followed channels').click()
  const row = live.getByTestId('live-follow-row')
  await expect(row.getByRole('combobox')).toHaveCount(0)
  await expect(row.getByText('Notify me')).toBeVisible()
  await expect(live.getByText('Start Live automatically')).toHaveCount(0)
  await expect(row.getByRole('switch', { name: /every hour/ })).toBeVisible()
  expect(m.unmocked).toEqual([])
})

test('a live channel shows its title and offers Start Live, which shows the session', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.items = [followRow(1, { state: 'live', last_status: 'Live now', live_now: true, stream_title: 'Late night chat', last_checked_at: Date.now() / 1000 })]
  const live = await openLive(page)
  await live.getByText('Followed channels').click()
  await expect(live.getByTestId('live-follow-status')).toContainText('Live now: Late night chat')
  await live.getByRole('button', { name: 'Start Live' }).click()
  await expect(live.getByTestId('live-status')).toBeVisible()
  expect(m.follows.posts.map((p) => p.path)).toEqual(['/api/live/follows/1/start'])
  await expect.poll(() => m.polls.length).toBeGreaterThan(0)
  expect(m.unmocked).toEqual([])
})

test('the bell shows a scheduled start and starts Live from a notification', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.items = [followRow(4, { state: 'live', live_now: true })]
  await page.route('**/api/notifications', (route) => route.fulfill({ json: { items: [
    { id: 2, at: Date.now() / 1000, kind: 'live', text: 'Channel 4 is live now', follow_id: 4, action: 'start_live', starts_at: null },
    { id: 1, at: Date.now() / 1000 - 60, kind: 'live', text: 'Channel 9 scheduled a stream', follow_id: 9, action: null, starts_at: 1_900_000_000 },
  ] } }))
  await page.goto('/#/jobs')
  await page.getByRole('button', { name: /Notifications/ }).click()
  await expect(page.getByText(/^Starts at \d/)).toBeVisible()
  await page.screenshot({ path: `${SCREENS}/follows-bell.png` })
  await page.getByRole('button', { name: 'Start Live' }).click()
  await expect(page).toHaveURL(/#\/live$/)
  await expect(page.getByTestId('live-status')).toBeVisible()
  expect(m.follows.posts.map((p) => p.path)).toEqual(['/api/live/follows/4/start'])
  expect(m.state.sessions[0].session_id).toBe(SID)
})
