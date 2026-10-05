import { expect, test } from '@playwright/test'

import { ME, USER, maybeScreenshot, mockAuth } from './authMocks'

// Desktop: the sign-in gate (auth on) against mocked /api/auth/* routes.
// Nothing reaches Google; see authMocks.ts.

test.use({ viewport: { width: 1440, height: 900 } })

test('auth off: the app renders as before, no user menu, no Login page', async ({ page }) => {
  const s = await mockAuth(page, ME.authOff)
  await page.goto('/#/library')
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  await expect(page.locator('details.user-menu')).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('shows Connecting… until /me answers', async ({ page }) => {
  const s = await mockAuth(page, ME.signedOut)
  let release!: () => void
  s.meGate = new Promise<void>((r) => (release = r))
  await page.goto('/#/settings')
  await expect(page.getByRole('status')).toHaveText('Connecting…')
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  await maybeScreenshot(page, 'desktop-connecting')
  release()
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('signed out: only the Login page; the button goes to /api/auth/login with the return path', async ({ page }) => {
  const s = await mockAuth(page, ME.signedOut)
  await page.goto('/#/settings')
  const button = page.getByRole('link', { name: 'Sign in with Google' })
  await expect(button).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(button).toHaveAttribute('href', '/api/auth/login?return_to=%2F%23%2Fsettings')
  await maybeScreenshot(page, 'desktop-login')
  await button.click()
  await expect(page.getByRole('heading', { name: 'Stand-in for Google' })).toBeVisible()
  expect(s.loginUrls).toHaveLength(1)
  expect(new URL(s.loginUrls[0]).searchParams.get('return_to')).toBe('/#/settings')
  expect(s.unmocked).toEqual([])
})

const ERRORS: [string, RegExp][] = [
  ['denied', /Sign-in was cancelled/],
  ['not_allowed', /isn't on this household's list/],
  ['expired', /took too long and expired/],
  ['provider_error', /Google sign-in didn't work just now/],
]

for (const [code, message] of ERRORS) {
  test(`login_error=${code} shows a plain message and is cleared from the address`, async ({ page }) => {
    const s = await mockAuth(page, ME.signedOut)
    await page.goto(`/?login_error=${code}`)
    await expect(page.getByRole('alert')).toHaveText(message)
    await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
    await expect.poll(() => new URL(page.url()).search).toBe('')
    if (code === 'not_allowed') await maybeScreenshot(page, 'desktop-login-error-not-allowed')
    expect(s.unmocked).toEqual([])
  })
}

test("sign-in not set up on the PC: says so, no button", async ({ page }) => {
  const s = await mockAuth(page, ME.notConfigured)
  await page.goto('/')
  await expect(page.getByText("Sign-in isn't set up on the PC yet.")).toBeVisible()
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toHaveCount(0)
  await maybeScreenshot(page, 'desktop-login-not-configured')
  expect(s.unmocked).toEqual([])
})

test('signed in: header menu shows the email; Sign out posts with CSRF and returns to Login', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'baihe_csrf', value: 'csrf-token-123', url: baseURL! }])
  const s = await mockAuth(page, ME.signedIn)
  await page.goto('/#/library')
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
  const menu = page.locator('details.user-menu')
  await expect(menu.locator('summary')).toContainText(USER.email!)
  await menu.locator('summary').click()
  await expect(page.getByTestId('user-email')).toHaveText(USER.email!)
  await maybeScreenshot(page, 'desktop-signed-in-menu')
  // Escape closes it.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeHidden()
  await menu.locator('summary').click()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  expect(s.logoutHeaders).toHaveLength(1)
  expect(s.logoutHeaders[0]['x-csrf-token']).toBe('csrf-token-123')
  expect(s.logoutHeaders[0]['x-baihe-local']).toBe('1')
  expect(s.unmocked).toEqual([])
})

test('a 401 from any call swaps in the Login page', async ({ page }) => {
  const s = await mockAuth(page, ME.signedIn)
  s.unauthorizedPaths.add('/api/library/dramas')
  await page.goto('/#/library')
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test.describe('below 1024px', () => {
  test.use({ viewport: { width: 1000, height: 800 } })

test('signed in: the cogwheel sits in the header\'s right-hand group, not in the Main nav, and its menu stays on screen', async ({ page }) => {
  await mockAuth(page, ME.signedIn)
  await page.goto('/#/library')
  const gear = page.locator('summary[aria-label="Settings and tools"]')
  await expect(gear).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' }).locator('.gear-menu')).toHaveCount(0)
  await expect(page.locator('.app-header .header-end .gear-menu')).toHaveCount(1)
  await maybeScreenshot(page, 'desktop-header')
  await gear.click()
  const panel = page.locator('.gear-menu-panel')
  await expect(panel).toBeVisible()
  const box = (await panel.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  await maybeScreenshot(page, 'desktop-gear-open')
})
})
