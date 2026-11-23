import type { Page, Route } from '@playwright/test'

import { expect, test } from './fixtures'

// The top bar: icon-only buttons, the Connected word, the Alerts name and the Report dialog's place.

const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
const NOW = () => Math.floor(Date.now() / 1000)

async function dock(page: Page, mode: 'docked' | 'float' | 'minimized' = 'docked') {
  await page.addInitScript((m) => localStorage.setItem('baihe.navDock', m), mode)
}

async function quietHeader(page: Page) {
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [] }))
  await page.route((u) => u.pathname === '/api/notifications', (r) => json(r, { items: [] }))
}

test('Connected: nothing before the first check answers, then Connected, Can\'t reach Baihe when a check fails, and Connected again', async ({ page }) => {
  await dock(page)
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let failing = false
  // Every /api/meta read waits, so whichever part of the page asks first (the status check is one of several) is held.
  await page.route((u) => u.pathname === '/api/meta', async (route) => {
    await held
    if (failing) return route.abort()
    return route.fallback()
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await expect(page.locator('.header-end')).toBeVisible()
  await expect(page.getByTestId('api-connected')).toHaveCount(0)
  await expect(page.getByTestId('api-status')).toHaveCount(0)

  release()
  await expect(page.getByTestId('api-connected')).toHaveText('● Connected')
  await expect(page.getByTestId('api-status')).toHaveCount(0)

  failing = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByTestId('api-status')).toBeVisible()
  await expect(page.getByTestId('api-connected')).toHaveCount(0)

  failing = false
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByTestId('api-connected')).toBeVisible()
  await expect(page.getByTestId('api-status')).toHaveCount(0)
})

test('Connected is quiet for a screen reader: the dot is hidden and it is not a live region', async ({ page }) => {
  await dock(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  const connected = page.getByTestId('api-connected')
  await expect(connected).toBeVisible()
  await expect(connected.locator('[aria-hidden="true"]')).toHaveText('●')
  await expect(connected.locator('xpath=ancestor-or-self::*[@role="status" or @aria-live]')).toHaveCount(0)
})

test('Alerts: the name with and without new rows, and the panel heading', async ({ page }) => {
  await dock(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [] }))
  const items = [1, 2].map((id) => ({ id, at: NOW() - id, kind: 'job_done', text: `Export finished ${id}` }))
  await page.route((u) => u.pathname === '/api/notifications', (r) => json(r, { items }))
  await page.goto('/#/library')
  const bell = page.getByRole('button', { name: /^Alerts/ })
  await expect(bell).toHaveAccessibleName('Alerts, 2 new')
  await expect(page.getByTestId('notify-count')).toHaveText('2')
  await bell.click()
  await expect(page.getByRole('region', { name: 'Recent alerts' })).toBeVisible()
  await expect(page.getByTestId('notify-count')).toHaveCount(0)
  await expect(bell).toHaveAccessibleName('Alerts')
  await expect(page.getByText('Recent notifications')).toHaveCount(0)
})

test('docked at 1440 every header button is an icon with its old words as name and tooltip', async ({ page }) => {
  await dock(page)
  await quietHeader(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  const end = page.locator('.header-end')
  await expect(end).toBeVisible()
  for (const [selector, name] of [
    ['button.palette-btn', 'Search pages and titles'],
    ['button.report-btn', 'Report a problem'],
    ['button.jobs-menu-btn', /^Jobs/],
    ['button.notify-bell-btn', /^Alerts/],
    ['button.keys-btn', 'Keyboard shortcuts'],
    ['button.theme-menu-btn', /^Theme/],
  ] as const) {
    const button = end.locator(selector)
    await expect(button).toHaveAccessibleName(name)
    expect(await button.evaluate((el) => (el as HTMLElement).innerText.trim())).toBe('')
    await expect(button.locator('svg')).toBeVisible()
  }
})

test('the Report a problem dialog opens centred, not against the left edge', async ({ page }) => {
  await dock(page)
  await quietHeader(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.locator('button.report-btn').click()
  const box = await page.locator('dialog.sheet[open]').boundingBox()
  expect(box).not.toBeNull()
  expect(Math.abs(box!.x + box!.width / 2 - 720)).toBeLessThan(10) // the reserved scrollbar gutter shifts the centre by half its width
})
