import { expect, test, type Page } from '@playwright/test'

// Settings > Remote access: the public-address check (PC only). The
// ip-check routes are mocked; other reads go to the seeded API and any other
// write is aborted and recorded. The saved address is never shown back.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const SECRET = 'https://ip.example.net/?token=SECRET-DDNS-TOKEN'
const IP_CHECK = '/api/diagnostics/remote-health/ip-check'

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

interface Mock {
  configured: boolean
  bodies: { path: string; body: unknown; local: string | null }[]
  saveStatus: number
}

async function mockIpCheck(page: Page, configured: boolean, saveStatus = 200): Promise<Mock> {
  const m: Mock = { configured, bodies: [], saveStatus }
  await page.route((u) => u.pathname.startsWith(IP_CHECK), async (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === IP_CHECK) return route.fulfill({ json: { configured: m.configured } })
    if (r.method() !== 'POST') return route.abort()
    m.bodies.push({ path, body: r.postDataJSON(), local: await r.headerValue('x-baihe-local') })
    if (path === IP_CHECK) {
      if (m.saveStatus !== 200) {
        return route.fulfill({ status: m.saveStatus, json: { error: { code: 'forbidden', message: 'Not allowed from this connection.' } } })
      }
      m.configured = true
    } else if (path === `${IP_CHECK}/clear`) {
      m.configured = false
    } else if (path === `${IP_CHECK}/test`) {
      return route.fulfill({
        json: { configured: true, state: 'warn', message: 'The public name points to an old address. Check the dynamic DNS updater.' },
      })
    }
    return route.fulfill({ json: { configured: m.configured } })
  })
  return m
}

async function open(page: Page) {
  await page.goto('/#/settings')
  const section = page.getByRole('region', { name: 'Remote access' })
  await expect(section).toBeVisible()
  return section
}

test('set the address check, test it, then clear it; the address is never shown back', async ({ page }) => {
  const unmocked = await guard(page)
  const m = await mockIpCheck(page, false)
  const section = await open(page)
  await expect(section.locator('.card-meta')).toHaveText('Address check not set')
  await expect(section.getByRole('button', { name: 'Test' })).toHaveCount(0)

  const input = section.getByRole('textbox', { name: 'Public address check' })
  await expect(input).toHaveValue('')
  await input.fill('http://ip.example.net/')
  await expect(section.getByText('The address must start with https:// and have no spaces.')).toBeVisible()
  await expect(section.getByRole('button', { name: 'Save public address check' })).toBeDisabled()

  await input.fill(SECRET)
  await section.getByRole('button', { name: 'Save public address check' }).click()
  await section.getByRole('button', { name: 'Confirm save public address check' }).click()
  await expect(section.locator('.card-meta')).toHaveText('Address check set')
  await expect(section.getByText('Saved. The next check uses it.')).toBeVisible()
  await expect(input).toHaveValue('')
  expect(await page.content()).not.toContain('SECRET-DDNS-TOKEN')

  await section.getByRole('button', { name: 'Test' }).click()
  const result = section.getByTestId('ip-check-test-result')
  await expect(result).toContainText('Needs attention')
  await expect(result).toContainText('points to an old address')

  await section.getByRole('button', { name: 'Clear public address check' }).click()
  await section.getByRole('button', { name: 'Confirm clear public address check' }).click()
  await expect(section.locator('.card-meta')).toHaveText('Address check not set')
  await expect(section.getByText('Cleared.')).toBeVisible()

  expect(m.bodies.map((b) => b.path)).toEqual([IP_CHECK, `${IP_CHECK}/test`, `${IP_CHECK}/clear`])
  expect(m.bodies[0].body).toEqual({ value: SECRET, confirm: true })
  expect(m.bodies[1].local).toBe('1')
  expect(m.bodies[2].body).toEqual({ confirm: true })
  expect(unmocked).toEqual([])
})

test('a refused save explains key writes and keeps nothing', async ({ page }) => {
  const unmocked = await guard(page)
  await mockIpCheck(page, false, 403)
  const section = await open(page)
  const input = section.getByRole('textbox', { name: 'Public address check' })
  await input.fill(SECRET)
  await section.getByRole('button', { name: 'Save public address check' }).click()
  await section.getByRole('button', { name: 'Confirm save public address check' }).click()
  await expect(section.getByText(/only be set on the Baihe PC itself/)).toBeVisible()
  await expect(input).toHaveValue('')
  await expect(section.locator('.card-meta')).toHaveText('Address check not set')
  expect(await page.content()).not.toContain('SECRET-DDNS-TOKEN')
  expect(unmocked).toEqual([])
})

test('away from the PC the card says PC only and makes no calls', async ({ page }) => {
  const unmocked = await guard(page)
  const calls: string[] = []
  await page.route((u) => u.pathname.startsWith(IP_CHECK), (route) => {
    calls.push(route.request().url())
    return route.abort()
  })
  await page.route('**/api/meta', async (route) => {
    const resp = await route.fetch()
    const body = await resp.json()
    return route.fulfill({ response: resp, json: { ...body, local: false } })
  })
  await page.goto('/#/settings')
  const section = page.getByRole('region', { name: 'Remote access' })
  await expect(section.locator('.card-meta')).toHaveText('PC only')
  await expect(section.getByText('Run this on the main PC.')).toBeVisible()
  await expect(section.getByRole('textbox')).toHaveCount(0)
  expect(calls).toEqual([])
  expect(unmocked).toEqual([])
})
