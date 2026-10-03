import { expect, test, type Page } from '@playwright/test'

import { mockDiskUsage } from './diskUsageMocks'

// Phone project (390x844, touch): Disk usage has no sideways scroll and
// 44px targets. DISK_SHOTS_DIR=<dir> also saves review screenshots.

const SHOTS = process.env.DISK_SHOTS_DIR

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function open(page: Page) {
  await page.goto('/#/library-tools')
  await page.locator('summary', { hasText: 'Disk usage' }).click()
  return page.getByRole('region', { name: 'Disk usage', exact: true })
}

async function shortTargets(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) }))
      .filter((x) => x.h < 43.5))
}

test('root and a drilled folder: no sideways scroll, 44px targets', async ({ page }) => {
  await mockDiskUsage(page)
  const sec = await open(page)
  await expect(sec.locator('.du-list .du-name').first()).toHaveText('library')
  await noSideways(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-root-phone.png`, fullPage: true })

  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open dramas' }).click()
  await sec.locator('.du-row', { hasText: '12' }).getByRole('button', { name: 'Clear 12' }).scrollIntoViewIfNeeded()
  await noSideways(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  const box = (await sec.getByTestId('du-treemap').boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-dramas-phone.png`, fullPage: true })
})

test('the two-step confirm and the move form fit the phone', async ({ page }) => {
  await mockDiskUsage(page)
  const sec = await open(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open backups' }).click()
  const auto = sec.locator('.du-row', { has: page.locator('.du-name', { hasText: /^auto$/ }) })
  await auto.getByRole('button', { name: 'Move…' }).first().click()
  await auto.getByRole('textbox', { name: 'New folder for auto' }).fill('D:\\A very long folder name for the backup copies\\Baihe')
  await auto.getByRole('button', { name: 'Move auto' }).tap()
  await expect(auto.getByRole('button', { name: /Confirm: move auto/ })).toBeVisible()
  await noSideways(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-move-phone.png`, fullPage: true })
})
