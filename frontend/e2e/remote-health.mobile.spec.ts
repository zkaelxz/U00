import { expect, test } from '@playwright/test'

import { ME } from './authMocks'

// The remote-access banner on a phone (390x844, touch) at the PC: 44px
// targets and no sideways scroll. Every /api request is fulfilled or aborted
// here (nothing reaches the seeded API).

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const NOW = Math.floor(Date.now() / 1000)
const MESSAGE = 'The certificate expires in 3 days and has not been renewed. Check Caddy\'s log.'
const CRITICAL = {
  state: 'critical', message: MESSAGE, checked_at: NOW - 60, since: NOW - 60,
  certificate: { state: 'critical', message: MESSAGE, days_left: 3 },
  ddns: { state: 'not_configured', message: 'The public address check is not set up.', configured: false },
  listener: { state: 'ok', message: 'The household listener is answering.' },
}

test('Phone at the PC: the banner fits and its buttons are 44px', async ({ page }) => {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'GET' && path === '/api/meta') {
      return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: true } })
    }
    if (r.method() === 'GET' && path === '/api/auth/me') return route.fulfill({ json: ME.authOff })
    if (r.method() === 'GET' && path === '/api/diagnostics/remote-health') return route.fulfill({ json: CRITICAL })
    if (r.method() === 'GET') {
      return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'Not mocked in this test.' } } })
    }
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.goto('/')
  const banner = page.getByTestId('remote-health-banner')
  await expect(banner).toContainText('Remote access is not working')
  const heights = await banner.locator('button, a').evaluateAll((els) =>
    els.map((e) => ({ text: (e.textContent ?? '').trim(), h: e.getBoundingClientRect().height })))
  expect(heights.map((x) => x.text)).toEqual(['Diagnostics', 'Dismiss'])
  for (const { text, h } of heights) expect(h, `${text} height`).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  await banner.getByRole('button', { name: 'Dismiss' }).tap()
  await expect(banner).toHaveCount(0)
  expect(unmocked).toEqual([])
})
