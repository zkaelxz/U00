import { expect, test } from '@playwright/test'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone (390x844, touch): the header bell is a 44px button on the title row,
// and its panel fits the screen with the page's 16px gutters. The list is
// mocked; NOTIFY_SCREENS_DIR saves a review screenshot (not asserted).

test('phone: the bell sits on the title row and its panel fits the screen', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000)
  await page.route((u) => u.pathname === '/api/notifications', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          { id: 2, at: now - 60 * 7, kind: 'job_failed', text: 'Translate failed: The Scum Villain’s Self-Saving System and Its Very Long Subtitle' },
          { id: 1, at: now - 3600 * 5, kind: 'chapters', text: '12 new chapters found for a very long tracked web novel title' },
        ],
      }),
    }),
  )
  await page.goto('/#/library')
  const bell = page.getByRole('button', { name: 'Alerts (2 new)' })
  await expect(bell).toBeVisible()
  const box = (await bell.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  const title = (await page.locator('.app-brand, .rail-brand').boundingBox())!
  expect(box.y).toBeLessThan(title.y + title.height) // on the title row
  expect(box.y + box.height).toBeGreaterThan(title.y)
  await expectNoSidewaysScroll(page)

  await bell.tap()
  const panel = page.getByRole('region', { name: 'Recent alerts' })
  await expect(panel.getByRole('listitem')).toHaveCount(2)
  const p = (await panel.boundingBox())!
  const width = page.viewportSize()!.width
  expect(p.x).toBeGreaterThanOrEqual(15)
  expect(p.x + p.width).toBeLessThanOrEqual(width - 15)
  expect(p.y).toBeGreaterThanOrEqual(box.y + box.height - 1) // under the bell
  await expectNoSidewaysScroll(page)
  if (process.env.NOTIFY_SCREENS_DIR) {
    await page.screenshot({ path: `${process.env.NOTIFY_SCREENS_DIR.replace(/\/$/, '')}/bell-phone.png` })
  }

  // Tapping elsewhere closes it; the count stays cleared.
  await page.locator('.app-brand, .rail-brand').tap()
  await expect(panel).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Alerts', exact: true })).toBeVisible()
})

test('phone: the Open link of a waiting row is a 44px target and opens the Jobs page', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000)
  await page.route((u) => u.pathname === '/api/notifications', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          { id: 1, at: now - 60, kind: 'waiting', text: 'Waiting for you: The queue stopped because an item failed. Start the queue to go on.', target: { page: 'jobs' } },
        ],
      }),
    }),
  )
  await page.goto('/#/library')
  await page.getByRole('button', { name: /^Alerts/ }).tap()
  const panel = page.getByRole('region', { name: 'Recent alerts' })
  const open = panel.getByRole('link', { name: 'Open' })
  await expect(open).toBeVisible()
  const box = (await open.boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(44)
  expect(box.width).toBeGreaterThanOrEqual(44)
  await expectNoSidewaysScroll(page)
  for (const width of [360, 320]) {
    await page.setViewportSize({ width, height: 844 })
    await expect(open).toBeVisible()
    await expectNoSidewaysScroll(page)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await open.tap()
  await expect(page).toHaveURL(/#\/jobs$/)
  await expect(panel).toHaveCount(0)
  await expectNoSidewaysScroll(page)
})
