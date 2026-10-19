import { expect, test, type Page } from '@playwright/test'

import { openFold } from './foldHelpers'
import { mockDiskUsage } from './diskUsageMocks'
import { installHitArea } from './hitArea'
import { expectPickedTab, pickTab } from './tabPicker'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Storage and Data folder tabs have no sideways scroll and
// 44px targets. DISK_SHOTS_DIR=<dir> also saves review screenshots.

const SHOTS = process.env.DISK_SHOTS_DIR

async function open(page: Page, tab: 'data' | 'storage' = 'data') {
  await page.goto(`/#/library-tools?tab=${tab}`)
  if (tab === 'storage') {
    await openFold(page, 'Trash')
  }
  return page.getByRole('tabpanel')
}

async function shortTargets(page: Page, selector: string) {
  return page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent ?? '').trim().slice(0, 30) }))
      .filter((x) => x.h < 43.5))
}

test('root and a drilled folder: no sideways scroll, 44px targets', async ({ page }) => {
  await mockDiskUsage(page)
  const sec = await open(page)
  await expect(sec.locator('.du-list .du-name').first()).toHaveText('library')
  await expectNoSidewaysScroll(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-root-phone.png`, fullPage: true })

  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open dramas' }).click()
  await sec.locator('.du-row', { hasText: '12' }).getByRole('button', { name: 'Move 12 to Trash' }).scrollIntoViewIfNeeded()
  await expectNoSidewaysScroll(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  const box = (await sec.getByTestId('du-treemap').boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-dramas-phone.png`, fullPage: true })
})

test('the Trash list and its typed-word confirms fit the phone', async ({ page }) => {
  await mockDiskUsage(page, { trash: [
    { path: 'library/dramas/12/a_very_long_folder_name_that_must_wrap_on_a_phone_screen', size: 21_000_000_000, files: 400, restorable: false },
    { path: 'library/tmp', size: 2_400_000_000, files: 80 },
  ] })
  const sec = await open(page, 'storage')
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash.getByTestId('trash-line')).toContainText('Trash uses 23.4 GB')
  await trash.getByRole('button', { name: 'Delete library/tmp permanently' }).tap()
  await expect(trash.getByLabel(/Type DELETE to confirm/)).toBeVisible()
  await trash.getByRole('button', { name: 'Empty Trash…' }).tap()
  await expectNoSidewaysScroll(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack, .du .delete-confirm input')).toEqual([])
  await trash.scrollIntoViewIfNeeded()
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-trash-phone.png`, fullPage: true })
})

test('the two-step confirm and the move form fit the phone', async ({ page }) => {
  await mockDiskUsage(page)
  const sec = await open(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open backups' }).click()
  const auto = sec.locator('.du-row', { has: page.locator('.du-name', { hasText: /^auto$/ }) })
  await auto.getByRole('button', { name: 'Move…' }).first().click()
  await auto.getByRole('textbox', { name: 'New folder for auto' }).fill('D:\\A very long folder name for the backup copies\\Baihe')
  await auto.getByRole('button', { name: 'Move auto', exact: true }).tap()
  await expect(auto.getByRole('button', { name: /Confirm: move auto/ })).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(await shortTargets(page, '.du button:not(.du-cell), .du .du-ack')).toEqual([])
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-move-phone.png`, fullPage: true })
})

test('the five sections are a picker; the page does not scroll sideways on any section', async ({ page }) => {
  await mockDiskUsage(page)
  await page.goto('/#/library-tools')
  await expect(page.getByRole('tablist', { name: 'Library tools' })).toHaveCount(0)
  for (const name of ['Organize', 'Activity', 'Backup', 'Storage', 'Data folder']) {
    await pickTab(page, name, 'Library tools')
    await expectPickedTab(page, 'Library tools', name)
    await expectNoSidewaysScroll(page)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/tab-${name.toLowerCase().replace(/\W+/g, '-')}-phone.png`, fullPage: true })
  }
  expect(await shortTargets(page, '.ui-tab-picker-btn')).toEqual([])
})
