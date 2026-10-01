import { type Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Settings > Notifications (Step 44) and the header bell. Every notification
// call is mocked and fulfilled; a catch-all aborts (and records) any other
// non-GET /api call, so nothing is written to the seeded library's .env and
// nothing is sent. NOTIFY_SCREENS_DIR saves review screenshots (not asserted).

const SECRET = 'https://discord.com/api/webhooks/123456789012345678/SECRETtokenABCDEFGHIJKLMNOP'

type Status = {
  discord_configured: boolean
  ntfy_configured: boolean
  ntfy_allow_local: boolean
  send_jobs: boolean
  send_chapters: boolean
  send_remote: boolean
}

const OFF: Status = {
  discord_configured: false,
  ntfy_configured: false,
  ntfy_allow_local: false,
  send_jobs: true,
  send_chapters: true,
  send_remote: true,
}

async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback() // not mocked: the real seeded API answers reads
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

async function mockNotifications(
  page: Page,
  status: Status,
  o: { saveStatus?: number; categoriesStatus?: number; categoriesGate?: Promise<void> } = {},
) {
  const bodies: { url: string; body: unknown }[] = []
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/api/settings/notifications**', async (route) => {
    const r = route.request()
    const url = new URL(r.url()).pathname
    if (r.method() === 'GET') return route.fulfill(json(status))
    bodies.push({ url, body: r.postDataJSON() })
    if (url.endsWith('/categories')) {
      if (o.categoriesGate) await o.categoriesGate
      if (o.categoriesStatus && o.categoriesStatus !== 200)
        return route.fulfill(json({ error: { code: 'internal_error', message: 'Boom.' } }, o.categoriesStatus))
      const b = r.postDataJSON() as { jobs?: boolean; chapters?: boolean; remote?: boolean }
      status = {
        ...status,
        ...(b.jobs !== undefined ? { send_jobs: b.jobs } : {}),
        ...(b.chapters !== undefined ? { send_chapters: b.chapters } : {}),
        ...(b.remote !== undefined ? { send_remote: b.remote } : {}),
      }
      return route.fulfill(json(status))
    }
    if (url.endsWith('/test'))
      return route.fulfill(json({ results: { discord: 'sent', ntfy: status.ntfy_configured ? 'failed' : 'not_configured' } }))
    const channel = url.split('/')[4] as 'discord' | 'ntfy'
    if (o.saveStatus && o.saveStatus !== 200)
      return route.fulfill(json({ error: { code: 'forbidden', message: 'Not allowed.' } }, o.saveStatus))
    const configured = !url.endsWith('/clear')
    status = { ...status, [`${channel}_configured`]: configured }
    return route.fulfill(json({ channel, configured }))
  })
  return bodies
}

async function open(page: Page) {
  await page.goto('/#/settings')
  const section = page.getByRole('region', { name: 'Notifications' })
  await expect(section).toBeVisible()
  return section
}

test('set a Discord webhook, send a test, then clear it; the address is never shown back', async ({ page }) => {
  const unmocked = await guard(page)
  const bodies = await mockNotifications(page, OFF)
  const section = await open(page)

  await expect(section.locator('.card-meta')).toHaveText('Off')
  await expect(section.getByTestId('notify-discord')).toHaveText('Missing')
  await expect(section.getByRole('button', { name: 'Send test' })).toBeDisabled()
  await expect(section.getByText('Set up Discord or ntfy first to send a test.')).toBeVisible()
  await expect(section.getByTestId('ntfy-local-note')).toContainText('BAIHE_NTFY_ALLOW_LOCAL=1')

  await expect(section.getByRole('textbox')).toHaveCount(0) // forms open one row at a time
  await section.getByRole('button', { name: 'Set up Discord' }).click()

  const input = section.getByRole('textbox', { name: 'Discord webhook address' })
  await expect(input).toHaveAttribute('type', 'password')
  await input.fill(SECRET)
  await section.getByRole('button', { name: 'Save Discord address' }).click()
  await section.getByRole('button', { name: 'Confirm save Discord address' }).click()
  await expect(section.getByTestId('notify-discord')).toHaveText('Set')
  await expect(section.locator('.card-meta')).toHaveText('On: Discord')
  await expect(input).toHaveValue('')
  await expect(section.getByText('Saved.')).toBeVisible()
  expect(bodies[0]).toEqual({ url: '/api/settings/notifications/discord', body: { value: SECRET, confirm: true } })
  expect(await page.content()).not.toContain('SECRETtoken')

  await section.getByRole('button', { name: 'Send test' }).click()
  await expect(section.getByTestId('notify-test-result')).toHaveText('Discord: sent.')

  await section.getByRole('button', { name: 'Clear Discord address' }).click()
  await section.getByRole('button', { name: 'Confirm clear Discord address' }).click()
  await expect(section.getByTestId('notify-discord')).toHaveText('Missing')
  expect(bodies.map((b) => b.url)).toEqual([
    '/api/settings/notifications/discord',
    '/api/settings/notifications/test',
    '/api/settings/notifications/discord/clear',
  ])
  expect(unmocked).toEqual([])
})

test('a refused save explains key writes and keeps nothing', async ({ page }) => {
  const unmocked = await guard(page)
  await mockNotifications(page, { ...OFF, ntfy_configured: true, ntfy_allow_local: true }, { saveStatus: 403 })
  const section = await open(page)
  await expect(section.getByTestId('notify-ntfy')).toHaveText('Set')
  await expect(section.getByTestId('ntfy-local-note')).toContainText('is allowed')
  await section.getByRole('button', { name: 'Replace ntfy' }).click()
  const input = section.getByRole('textbox', { name: 'ntfy topic address' })
  await input.fill('https://ntfy.sh/secret-topic-name')
  await section.getByRole('button', { name: 'Save ntfy address' }).click()
  await section.getByRole('button', { name: 'Confirm save ntfy address' }).click()
  await expect(section.getByText(/only be set on the Baihe PC itself/)).toBeVisible()
  await expect(input).toHaveValue('')
  expect(await page.content()).not.toContain('secret-topic-name')
  expect(unmocked).toEqual([])
})

test('away from the PC the section says PC only and makes no notification calls', async ({ page }) => {
  const unmocked = await guard(page)
  const calls: string[] = []
  await page.route('**/api/settings/notifications**', (route) => {
    calls.push(route.request().url())
    return route.abort()
  })
  await page.route('**/api/meta', async (route) => {
    const resp = await route.fetch()
    const body = await resp.json()
    return route.fulfill({ response: resp, json: { ...body, local: false } })
  })
  await page.goto('/#/settings')
  const section = page.getByRole('region', { name: 'Notifications' })
  await expect(section.locator('.card-meta')).toHaveText('PC only')
  await expect(section.getByText('Run this on the main PC.')).toBeVisible()
  await expect(section.getByRole('textbox', { name: 'Discord webhook address' })).toHaveCount(0)
  expect(calls).toEqual([])
  expect(unmocked).toEqual([])
})

test('What to send: each switch saves at once, sending only what changed', async ({ page }) => {
  const unmocked = await guard(page)
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  const bodies = await mockNotifications(page, { ...OFF, discord_configured: true, send_chapters: false }, { categoriesGate: gate })
  const section = await open(page)
  const group = section.getByRole('group', { name: 'What to send' })
  const jobs = group.getByRole('switch', { name: 'Jobs finished or failed' })
  const chapters = group.getByRole('switch', { name: 'New chapters found' })
  await expect(jobs).toBeChecked()
  await expect(chapters).not.toBeChecked()
  await expect(group.getByText('The bell at the top of the page always lists every event.')).toBeVisible()
  await expect(section.getByText(/finds new chapters/).first()).toBeVisible()

  await jobs.click()
  // Saving: both switches wait for the answer.
  await expect(jobs).not.toBeChecked()
  await expect(jobs).toBeDisabled()
  await expect(chapters).toBeDisabled()
  release()
  await expect(jobs).toBeEnabled()
  await expect(jobs).not.toBeChecked()

  await chapters.click()
  await expect(chapters).toBeChecked()
  await expect(chapters).toBeEnabled()
  if (process.env.NOTIFY_SCREENS_DIR) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await section.screenshot({ path: `${process.env.NOTIFY_SCREENS_DIR.replace(/\/$/, '')}/settings-notifications-desktop.png` })
  }
  expect(bodies).toEqual([
    { url: '/api/settings/notifications/categories', body: { jobs: false } },
    { url: '/api/settings/notifications/categories', body: { chapters: true } },
  ])
  expect(unmocked).toEqual([])
})

test('What to send: a failed save puts the switch back and says so', async ({ page }) => {
  const unmocked = await guard(page)
  await mockNotifications(page, OFF, { categoriesStatus: 500 })
  const section = await open(page)
  const jobs = section.getByRole('switch', { name: 'Jobs finished or failed' })
  await expect(jobs).toBeChecked()
  await jobs.click()
  await expect(section.getByRole('alert')).toBeVisible()
  await expect(jobs).toBeChecked()
  await expect(jobs).toBeEnabled()
  expect(unmocked).toEqual([])
})

// ---- header bell ----

const NOW = () => Math.floor(Date.now() / 1000)

async function mockBell(page: Page, items: { id: number; at: number; kind: string; text: string }[]) {
  const state = { items, calls: 0 }
  await page.route((u) => u.pathname === '/api/notifications', (route) => {
    state.calls += 1
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: state.items }) })
  })
  return state
}

test('header bell: unread count, the list, and opening marks them seen', async ({ page }) => {
  const unmocked = await guard(page)
  const now = NOW()
  const bell = await mockBell(page, [
    { id: 3, at: now - 120, kind: 'chapters', text: '2 new chapters found for Signal' },
    { id: 2, at: now - 3 * 3600, kind: 'job_failed', text: 'Translate failed: Signal' },
    { id: 1, at: now - 4 * 3600, kind: 'job_done', text: 'Transcribe finished: Signal' },
  ])
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Notifications (3 new)' })
  await expect(button).toBeVisible()
  await expect(page.getByTestId('notify-count')).toHaveText('3')

  await button.click()
  const panel = page.getByRole('region', { name: 'Recent notifications' })
  await expect(panel).toBeVisible()
  const rows = panel.getByRole('listitem')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('New chapters')
  await expect(rows.nth(0)).toContainText('2 new chapters found for Signal')
  await expect(rows.nth(0)).toContainText('2 min ago')
  await expect(rows.nth(1)).toContainText('Failed')
  await expect(rows.nth(1)).toContainText('3 h ago')
  await expect(rows.nth(2)).toContainText('Done')
  await expect(rows.nth(0).locator('.notify-new')).toHaveText('New') // unread when opened
  await expect(panel.locator('.notify-new')).toHaveCount(3)
  await expect(panel.getByText('This list is kept until the app restarts.')).toBeVisible()
  // Opening marked them seen.
  await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByTestId('notify-count')).toHaveCount(0)
  if (process.env.NOTIFY_SCREENS_DIR) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.screenshot({ path: `${process.env.NOTIFY_SCREENS_DIR.replace(/\/$/, '')}/bell-desktop.png` })
  }

  // Escape closes and hands focus back.
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeFocused()

  // Reopening (and a reload) shows nothing new; items stay listed.
  await page.reload()
  const plain = page.getByRole('button', { name: 'Notifications', exact: true })
  await expect(plain).toBeVisible()
  await expect(page.getByTestId('notify-count')).toHaveCount(0)
  await plain.click()
  await expect(panel.getByRole('listitem')).toHaveCount(3)
  await expect(panel.locator('.notify-new')).toHaveCount(0)
  // A click elsewhere closes it.
  await page.getByRole('heading', { name: 'Baihe Studio', level: 1 }).click()
  await expect(panel).toHaveCount(0)

  // A new event shows up when the window regains focus.
  bell.items = [{ id: 4, at: NOW(), kind: 'job_done', text: 'Export finished: Signal' }, ...bell.items]
  const before = bell.calls
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByRole('button', { name: 'Notifications (1 new)' })).toBeVisible()
  expect(bell.calls).toBeGreaterThan(before)
  expect(unmocked).toEqual([])
})

test('header bell: empty list, and hidden when the list is refused', async ({ page }) => {
  const unmocked = await guard(page)
  await mockBell(page, [])
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Notifications', exact: true })
  await button.click()
  const panel = page.getByRole('region', { name: 'Recent notifications' })
  await expect(panel.getByText('Nothing yet. Finished and failed jobs show up here.')).toBeVisible()
  await expect(panel.getByRole('listitem')).toHaveCount(0)

  await page.route((u) => u.pathname === '/api/notifications', (route) =>
    route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'Not allowed.' } }) }),
  )
  await page.reload()
  await expect(page.getByRole('button', { name: 'Report a problem' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Notifications/ })).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('header bell: fits the one-row header at 1280px, and its panel stays on screen at 1024px', async ({ page }) => {
  const unmocked = await guard(page)
  await mockBell(page, [{ id: 1, at: NOW() - 30, kind: 'job_done', text: 'Transcribe finished: Signal' }])
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/library')
    await expect(page.getByTestId('api-status')).toBeVisible()
    const bell = page.getByRole('button', { name: /^Notifications/ })
    const b = (await bell.boundingBox())!
    const nav = (await page.getByRole('navigation', { name: 'Main' }).boundingBox())!
    // 1280: the bell did not push the header onto a second row.
    if (width === 1280) expect(b.y).toBeLessThan(nav.y + nav.height)
    await bell.click()
    const p = (await page.getByRole('region', { name: 'Recent notifications' }).boundingBox())!
    expect(p.x).toBeGreaterThanOrEqual(0)
    expect(p.x + p.width).toBeLessThanOrEqual(width)
    await page.keyboard.press('Escape')
  }
  expect(unmocked).toEqual([])
})
