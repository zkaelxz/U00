import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { mockLibrarySharing } from './librarySharingMocks'
import { hitHeight, installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone (390x844): the sharing control keeps 44px targets and the page does not scroll sideways.

test('sharing control fits a phone with a 44px target', async ({ page }) => {
  const s = await mockLibrarySharing(page, ME.signedIn)
  await page.goto('/')
  const card = page.locator('li.drama-card', { hasText: 'Hidden Letters' })
  const button = card.getByRole('button', { name: 'Share with household: Hidden Letters' })
  await expect(button).toBeVisible()
  expect((await hitHeight(button))).toBeGreaterThanOrEqual(44)
  await button.click()
  await expect(card.getByText('Shared', { exact: true })).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/library-sharing-phone.png` })
  expect(s.unmocked).toEqual([])
})
