import { expect, test, type Page } from '@playwright/test'

import { mockDiskUsage } from './diskUsageMocks'

// Library tools > Disk usage, desktop. The API is mocked (nothing on disk is
// touched). DISK_SHOTS_DIR=<dir> also saves review screenshots.

const SHOTS = process.env.DISK_SHOTS_DIR

async function openSection(page: Page) {
  await page.goto('/#/library-tools')
  const summary = page.locator('summary', { hasText: 'Disk usage' })
  const details = page.locator('details.section', { has: summary })
  if ((await details.getAttribute('open')) === null) await summary.click()
  return page.getByRole('region', { name: 'Disk usage', exact: true })
}

const row = (page: Page, name: string) => page.locator('.du-row', { has: page.locator('.du-name', { hasText: new RegExp(`^${name}$`) }) })

test('nothing is scanned until the section is opened; then the root shows biggest first', async ({ page }) => {
  let scans = 0
  await mockDiskUsage(page)
  await page.route('**/api/data-usage', async (r) => { scans += 1; await r.fallback() })
  await page.goto('/#/library-tools')
  await expect(page.locator('summary', { hasText: 'Disk usage' })).toBeVisible()
  expect(scans).toBe(0)
  const sec = await openSection(page)
  const names = await sec.locator('.du-list .du-name').allTextContents()
  expect(names).toEqual(['library', 'model_cache', '.env'])
  await expect(sec).toContainText('2.8 GB free of 252.0 GB on this drive')
  await expect(sec.getByTestId('du-treemap').locator('.du-cell')).toHaveCount(3)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-root-desktop.png`, fullPage: true })
})

test('drill down with the breadcrumb, protected items are disabled with their reason', async ({ page }) => {
  await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await expect(sec.locator('.du-crumbs [aria-current="page"]')).toHaveText('library')
  const db = row(page, 'library.db')
  await expect(db).toContainText('Protected')
  await expect(db).toContainText('The Baihe database.')
  await expect(db.getByRole('button', { name: 'Clear library.db' })).toBeDisabled()
  const dramas = row(page, 'dramas')
  await expect(dramas).toContainText("Can't be recreated")
  await sec.getByRole('button', { name: 'Open dramas' }).click()
  await expect(sec.locator('.du-crumbs li')).toHaveCount(3)
  await sec.locator('.du-crumbs').getByRole('button', { name: 'Data folder' }).click()
  await expect(sec.locator('.du-crumbs li')).toHaveCount(1)
  // Clicking a treemap cell drills in too.
  await sec.getByTestId('du-treemap').locator('.du-cell', { hasText: 'library' }).click()
  await expect(sec.locator('.du-crumbs [aria-current="page"]')).toHaveText('library')
})

test('clear is two steps, names the size, sends what was seen and rescans', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  const tmp = row(page, 'tmp')
  await expect(tmp).toContainText('Temporary job files: rebuilt by Baihe')
  await tmp.getByRole('button', { name: 'Clear tmp' }).click()
  const confirm = tmp.getByRole('button', { name: 'Confirm: send tmp (2.4 GB) to the Recycle Bin' })
  await expect(confirm).toBeVisible()
  expect(mock.posts).toHaveLength(0)
  await confirm.click()
  await expect(sec.getByRole('status').filter({ hasText: 'Sent tmp to the Recycle Bin. Freed 2.4 GB' })).toBeVisible()
  expect(mock.posts[0].body).toEqual({ path: 'library/tmp', confirm: true, expected_size_bytes: 2_400_000_000, expected_file_count: 80 })
  await expect(row(page, 'tmp')).toHaveCount(0)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-cleared-desktop.png`, fullPage: true })
})

test('title media needs the extra tick before Clear works', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open dramas' }).click()
  const t7 = row(page, '7')
  const clear = t7.getByRole('button', { name: 'Clear 7' })
  await expect(clear).toBeDisabled()
  await t7.getByLabel(/I understand 7 is source media/).check()
  await expect(clear).toBeEnabled()
  await clear.click()
  await t7.getByRole('button', { name: /Confirm: send 7/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Sent 7 to the Recycle Bin' })).toBeVisible()
  expect(mock.posts[0].body.confirm_irreplaceable).toBe(true)
})

test('a changed item answers 409: the message shows and the list refreshes', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  mock.tree['library'].find((n) => n.name === 'tmp')!.size = 9
  await page.getByRole('button', { name: 'Clear tmp' }).click()
  await page.getByRole('button', { name: /Confirm: send tmp/ }).click()
  await expect(sec.getByRole('alert')).toContainText('This item changed since you looked')
  await expect(row(page, 'tmp')).toContainText('9 B')
})

test('only the backup folder is movable; the rest says why', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await expect(row(page, 'dramas')).toContainText("Can't be moved")
  await sec.getByRole('button', { name: 'Open backups' }).click()
  await expect(row(page, 'exports')).toContainText("Can't be moved")
  const auto = row(page, 'auto')
  await auto.getByRole('button', { name: 'Move…' }).first().click()
  const dest = auto.getByRole('textbox', { name: 'New folder for auto' })
  const confirmBtn = auto.getByRole('button', { name: 'Move auto' })
  await expect(confirmBtn).toBeDisabled()
  await dest.fill('D:\\Baihe backups')
  await confirmBtn.click()
  await auto.getByRole('button', { name: /Confirm: move auto \(2\.9 GB\)/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved auto (2.9 GB). Baihe now uses the new folder.' })).toBeVisible()
  expect(mock.posts[0]).toEqual({ path: 'move', body: { path: 'library/backups/auto', destination: 'D:\\Baihe backups', confirm: true } })
})

test('busy library, partial scan, empty folder and no Recycle Bin are explained', async ({ page }) => {
  const mock = await mockDiskUsage(page, { recycleAvailable: false })
  mock.setBusy('A job, restore or other library task is running. Wait for it to finish, then try again.')
  mock.setPartial(true)
  const sec = await openSection(page)
  await expect(sec).toContainText('Counting took too long and was stopped.')
  await expect(sec).toContainText('A job, restore or other library task is running.')
  await expect(page.getByRole('button', { name: 'Clear model_cache' })).toBeDisabled()
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open tmp' }).click()
  await expect(sec).toContainText('This folder is empty.')
})

test('without a Recycle Bin, Clear is disabled and says why', async ({ page }) => {
  await mockDiskUsage(page, { recycleAvailable: false })
  const sec = await openSection(page)
  await expect(page.getByRole('button', { name: 'Clear model_cache' })).toBeDisabled()
  await expect(row(page, 'model_cache')).toContainText('this system has none')
  await expect(sec).not.toContainText('A job, restore')
})

test('a slow scan can be cancelled, and Rescan tries again', async ({ page }) => {
  await mockDiskUsage(page, { slow: true })
  const sec = await openSection(page)
  await expect(sec.getByText('Scanning…')).toBeVisible()
  await sec.getByRole('button', { name: 'Cancel' }).click()
  await expect(sec.getByText('Scan cancelled. Press Rescan to try again.')).toBeVisible()
  await sec.getByRole('button', { name: 'Rescan' }).click()
  await expect(sec.locator('.du-list .du-name').first()).toHaveText('library')
})

test('a failed scan shows an error and Rescan recovers', async ({ page }) => {
  let fail = true
  await mockDiskUsage(page)
  await page.route('**/api/data-usage', async (r) => {
    if (fail) { fail = false; return r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'x' } } }) }
    return r.fallback()
  })
  const sec = await openSection(page)
  await expect(sec.getByRole('alert')).toBeVisible()
  await sec.getByRole('button', { name: 'Rescan' }).click()
  await expect(sec.locator('.du-list .du-name').first()).toHaveText('library')
})

test('away from the PC the section only says to use the main PC', async ({ page }) => {
  await page.route('**/api/meta', (r) => r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  let scans = 0
  await page.route('**/api/data-usage**', (r) => { scans += 1; return r.fulfill({ status: 403, json: {} }) })
  await page.goto('/#/library-tools')
  const summary = page.locator('summary', { hasText: 'Disk usage' })
  await summary.click()
  await expect(page.locator('details.section', { has: summary }).getByText('Run this on the main PC.')).toBeVisible()
  await expect(page.getByRole('button', { name: /Rescan|Clear/ })).toHaveCount(0)
  expect(scans).toBe(0)
})

test('keyboard: Tab reaches Open, Clear and the confirm; Escape backs out', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  const tmp = row(page, 'tmp')
  await tmp.getByRole('button', { name: 'Clear tmp' }).focus()
  await page.keyboard.press('Enter')
  const confirm = tmp.getByRole('button', { name: /Confirm: send tmp/ })
  await expect(confirm).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(tmp.getByRole('button', { name: 'Clear tmp' })).toBeFocused()
  expect(mock.posts).toHaveLength(0)
})
