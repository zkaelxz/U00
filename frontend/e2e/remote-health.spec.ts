import { expect, test, type Page } from '@playwright/test'

import { ME, REMOTE_HEALTH_OFF } from './authMocks'

// The remote-access health banner in the app shell and the line on
// Diagnostics (GET /api/diagnostics/remote-health). Every /api request is
// fulfilled or aborted here: /api/meta, /api/auth/me and remote-health are
// mocked, any other GET gets a mocked 404 and any other call is aborted and
// recorded (nothing reaches the seeded API).

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const NOT_MOCKED = { error: { code: 'not_found', message: 'Not mocked in this test.' } }
const NOW = Math.floor(Date.now() / 1000)

function health(state: string, message: string, since: number, days: number | null) {
  return {
    state, message, checked_at: NOW - 120, since,
    certificate: { state: state === 'ok' ? 'ok' : state, message, days_left: days },
    ddns: { state: 'not_configured', message: 'The public address check is not set up.', configured: false },
    listener: { state: 'ok', message: 'The household listener is answering.' },
  }
}

const WARN = health('warn', 'The certificate expires in 9 days and should already have been renewed. Check Caddy\'s log.', NOW - 3600, 9)
const CRITICAL = health('critical', 'The household listener is not answering on this PC.', NOW - 60, 9)
const OK = health('ok', 'Remote access is working.', NOW - 7200, 60)

interface Mock {
  body: unknown
  reads: number
  unmocked: string[]
}

async function mockPage(page: Page, body: unknown, local = true): Promise<Mock> {
  const m: Mock = { body, reads: 0, unmocked: [] }
  await page.route('**/api/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/meta') {
      return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local } })
    }
    if (r.method() === 'GET' && path === '/api/auth/me') return route.fulfill({ json: ME.authOff })
    if (r.method() === 'GET' && path === '/api/diagnostics/remote-health') {
      m.reads += 1
      return route.fulfill({ json: m.body })
    }
    if (r.method() === 'GET') return route.fulfill({ status: 404, json: NOT_MOCKED })
    m.unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return m
}

test('PC admin: a warning shows until dismissed, and comes back when the state changes', async ({ page }) => {
  const m = await mockPage(page, WARN)
  await page.goto('/')
  const banner = page.getByTestId('remote-health-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('Remote access needs attention')
  await expect(banner).toContainText('expires in 9 days')
  await expect(banner).toContainText('Last checked 2 min ago.')
  await expect(banner.getByRole('link', { name: 'Diagnostics' })).toHaveAttribute('href', /diagnostics/)

  await banner.getByRole('button', { name: 'Dismiss' }).click()
  await expect(banner).toHaveCount(0)
  // Still dismissed after a reload: the same problem.
  await page.reload()
  await expect.poll(() => m.reads).toBeGreaterThan(1)
  await expect(page.getByTestId('remote-health-banner')).toHaveCount(0)

  // It got worse: a new state, so the banner is back, as an alert.
  m.body = CRITICAL
  await page.reload()
  const again = page.getByRole('alert').filter({ hasText: 'Remote access is not working' })
  await expect(again).toBeVisible()
  await expect(again).toContainText('The household listener is not answering on this PC.')
  expect(m.unmocked).toEqual([])
})

test('PC admin: nothing shows while remote access is OK or off', async ({ page }) => {
  const m = await mockPage(page, OK)
  await page.goto('/')
  await expect.poll(() => m.reads).toBe(1)
  await expect(page.getByTestId('remote-health-banner')).toHaveCount(0)
  m.body = REMOTE_HEALTH_OFF
  await page.reload()
  await expect.poll(() => m.reads).toBe(2)
  await expect(page.getByTestId('remote-health-banner')).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('Away from the PC (household listener): no banner and no request', async ({ page }) => {
  const m = await mockPage(page, CRITICAL, false)
  // The Library list loads after /api/meta has said this is not the PC, which is when a health read would start.
  const libraryRead = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/library/dramas')
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Baihe Studio' })).toBeVisible()
  await libraryRead
  expect(m.reads).toBe(0)
  await expect(page.getByTestId('remote-health-banner')).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('Diagnostics shows a neutral OK or Off line', async ({ page }) => {
  const m = await mockPage(page, OK)
  await page.goto('/#/diagnostics')
  const line = page.getByTestId('remote-health-line')
  await expect(line).toContainText('Remote access: OK')
  await expect(line).toContainText('Certificate valid for 60 more days.')
  await expect(page.getByTestId('remote-health-banner')).toHaveCount(0)
  m.body = REMOTE_HEALTH_OFF
  await page.reload()
  await expect(page.getByTestId('remote-health-line')).toContainText('Remote access: Off')
  await expect(page.getByTestId('remote-health-line')).toContainText('not set up on this PC')
  expect(m.unmocked).toEqual([])
})
