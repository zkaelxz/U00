import { expect, test, type Page } from '@playwright/test'

// Phone (390x844, touch): the header bell is a 44px button on the title row,
// and its panel fits the screen with the page's 16px gutters. The list is
// mocked; NOTIFY_SCREENS_DIR saves a review screenshot (not asserted).

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

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
  const bell = page.getByRole('button', { name: 'Notifications (2 new)' })
  await expect(bell).toBeVisible()
  const box = (await bell.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  const nav = (await page.getByRole('navigation', { name: 'Main' }).boundingBox())!
  expect(box.y + box.height).toBeLessThanOrEqual(nav.y + 1) // title row, above the nav
  await noSideways(page)

  await bell.tap()
  const panel = page.getByRole('region', { name: 'Recent notifications' })
  await expect(panel.getByRole('listitem')).toHaveCount(2)
  const p = (await panel.boundingBox())!
  const width = page.viewportSize()!.width
  expect(p.x).toBeGreaterThanOrEqual(15)
  expect(p.x + p.width).toBeLessThanOrEqual(width - 15)
  expect(p.y).toBeGreaterThanOrEqual(box.y + box.height - 1) // under the bell
  await noSideways(page)
  if (process.env.NOTIFY_SCREENS_DIR) {
    await page.screenshot({ path: `${process.env.NOTIFY_SCREENS_DIR.replace(/\/$/, '')}/bell-phone.png` })
  }

  // Tapping elsewhere closes it; the count stays cleared.
  await page.getByRole('heading', { name: 'Baihe Studio', level: 1 }).tap()
  await expect(panel).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Notifications', exact: true })).toBeVisible()
})
