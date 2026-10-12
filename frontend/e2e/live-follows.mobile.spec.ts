import { expect, test, type Page } from '@playwright/test'

import { SCREENS, followRow, mockLive, openLive } from './liveMocks'

// Phone project (390x844, touch): the Followed channels card fits the
// screen and its controls are 44px targets.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('followed channels on a phone: no sideways scroll, 44px targets', async ({ page }) => {
  const m = await mockLive(page)
  m.follows.items = [
    followRow(1, { channel_name: 'A channel with a rather long display name that has to wrap on a phone', state: 'live', last_status: 'Live now · not started: another Live session is running', live_now: true, last_checked_at: Date.now() / 1000 }),
    followRow(2, { channel: 'channel/UC' + 'x'.repeat(22), channel_name: '', state: 'error', last_status: 'YouTube asked for a sign-in check.', mode: 'auto', last_checked_at: Date.now() / 1000 - 4000 }),
  ]
  const live = await openLive(page)
  await live.getByText('Followed channels').tap()
  await expect(live.getByTestId('live-follow-row')).toHaveCount(2)
  await noSideways(page)
  // A row's .btn-sm keeps its 32px box; its ::after widens the tap area to 44px (index.css).
  const small = await live.locator('.live-follows').locator('button:not(.link, .field-help-btn, .btn-sm), select, input')
    .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, what: e.getAttribute('aria-label') ?? e.textContent }))
      .filter((x) => x.h < 44))
  expect(small).toEqual([])
  const rowButtons = await live.locator('.live-follow-row .btn-sm').evaluateAll((els) =>
    els.map((e) => getComputedStyle(e, '::after').position))
  expect(rowButtons.length).toBeGreaterThan(0)
  expect(rowButtons.every((p) => p === 'absolute')).toBe(true)
  await page.screenshot({ path: `${SCREENS}/follows-phone.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})
