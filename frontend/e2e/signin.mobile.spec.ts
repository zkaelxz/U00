import { expect, test, type Page } from '@playwright/test'

import { ME, USER, maybeScreenshot, mockAuth } from './authMocks'

// Phone project (390x844, touch): the Login page and the signed-in header.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tall(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox()
  expect(box, selector).not.toBeNull()
  expect(box!.height, selector).toBeGreaterThanOrEqual(44)
}

test('phone: Login page fits, 44 px button, error shown', async ({ page }) => {
  const s = await mockAuth(page, ME.signedOut)
  await page.goto('/?login_error=not_allowed')
  await expect(page.getByRole('alert')).toContainText("isn't on this household's list")
  await tall(page, 'a.login-button')
  await noSideways(page)
  await maybeScreenshot(page, 'phone-login-error-not-allowed')
  await page.goto('/#/library')
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
  await maybeScreenshot(page, 'phone-login')
  expect(s.unmocked).toEqual([])
})

test('phone: sign-in not set up', async ({ page }) => {
  const s = await mockAuth(page, ME.notConfigured)
  await page.goto('/')
  await expect(page.getByText("Sign-in isn't set up on the PC yet.")).toBeVisible()
  await noSideways(page)
  await maybeScreenshot(page, 'phone-login-not-configured')
  expect(s.unmocked).toEqual([])
})

test('phone: signed-in header keeps the menu on the title row, 44 px targets, Sign out works', async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: 'baihe_csrf', value: 'phone-token', url: baseURL! }])
  const s = await mockAuth(page, ME.signedIn)
  await page.goto('/#/library')
  const summary = page.locator('details.user-menu > summary')
  await expect(summary).toContainText(USER.email!)
  await tall(page, 'details.user-menu > summary')
  const title = await page.getByRole('heading', { name: 'Baihe Studio' }).boundingBox()
  const menuBox = await summary.boundingBox()
  // Same row as the title (the nav wraps below).
  expect(Math.abs(menuBox!.y + menuBox!.height / 2 - (title!.y + title!.height / 2))).toBeLessThan(24)
  await noSideways(page)
  await summary.tap()
  const signOut = page.getByRole('button', { name: 'Sign out' })
  await expect(signOut).toBeVisible()
  await tall(page, '.user-menu-panel button')
  await noSideways(page)
  await maybeScreenshot(page, 'phone-signed-in-menu')
  await signOut.tap()
  await expect(page.getByRole('link', { name: 'Sign in with Google' })).toBeVisible()
  expect(s.logoutHeaders[0]['x-csrf-token']).toBe('phone-token')
  expect(s.unmocked).toEqual([])
})

test('signed in: the Menu button leads the header, replaces the cogwheel, and its drawer stays on screen', async ({ page }) => {
  await mockAuth(page, ME.signedIn)
  await page.goto('/#/library')
  await expect(page.locator('summary[aria-label="Settings and tools"]')).toHaveCount(0)
  const menu = page.getByRole('button', { name: 'Menu', exact: true })
  await expect(menu).toBeVisible()
  const mb = (await menu.boundingBox())!
  expect(mb.width).toBeGreaterThanOrEqual(44)
  expect(mb.height).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await maybeScreenshot(page, 'phone-header')
  await menu.tap()
  const drawer = page.getByRole('dialog', { name: 'Main menu' })
  await expect(drawer).toBeVisible()
  const box = (await drawer.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  await noSideways(page)
  await maybeScreenshot(page, 'phone-menu-open')
})
