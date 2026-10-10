import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test('the card fits a phone with 44px targets', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  const card = page.getByRole('region', { name: 'Get started' })
  await expect(card).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const el of [
    card.getByRole('button', { name: 'Dismiss' }),
    card.getByRole('link', { name: 'Discover' }),
    card.getByRole('link', { name: 'Sources' }),
  ]) {
    expect(await el.evaluate((n) => window.hitHeight(n))).toBeGreaterThanOrEqual(44)
  }
})
