import { expect, test, type Page } from '@playwright/test'

// Settings > Notifications (Step 44). Every notification call is mocked and
// fulfilled; a catch-all aborts (and records) any other non-GET /api call,
// so nothing is written to the seeded library's .env and nothing is sent.

const SECRET = 'https://discord.com/api/webhooks/123456789012345678/SECRETtokenABCDEFGHIJKLMNOP'

type Status = { discord_configured: boolean; ntfy_configured: boolean; ntfy_allow_local: boolean }

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

async function mockNotifications(page: Page, status: Status, o: { saveStatus?: number } = {}) {
  const bodies: { url: string; body: unknown }[] = []
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/api/settings/notifications**', (route) => {
    const r = route.request()
    const url = new URL(r.url()).pathname
    if (r.method() === 'GET') return route.fulfill(json(status))
    bodies.push({ url, body: r.postDataJSON() })
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
  const bodies = await mockNotifications(page, { discord_configured: false, ntfy_configured: false, ntfy_allow_local: false })
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
  await mockNotifications(page, { discord_configured: false, ntfy_configured: true, ntfy_allow_local: true }, { saveStatus: 403 })
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
