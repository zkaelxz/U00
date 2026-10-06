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
  await expect(sec.locator('.du-list .du-name')).toHaveText(['library', 'model_cache', '.env'])
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
  await expect(db.getByRole('button', { name: 'Move library.db to Trash' })).toBeDisabled()
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

test('move to Trash is two steps, names the size, says nothing is freed, and the item shows up in Trash', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  const tmp = row(page, 'tmp')
  await expect(tmp).toContainText('Temporary job files: rebuilt by Baihe')
  await expect(tmp.getByRole('button', { name: 'Move tmp to Trash' })).toHaveText('Move to Trash…')
  await tmp.getByRole('button', { name: 'Move tmp to Trash' }).click()
  const confirm = tmp.getByRole('button', { name: 'Confirm: move tmp (2.4 GB) to Trash' })
  await expect(confirm).toBeVisible()
  expect(mock.posts).toHaveLength(0)
  await confirm.click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved tmp (2.4 GB) to Trash. Nothing is freed until you delete it from Trash' })).toBeVisible()
  expect(mock.posts[0].body).toEqual({ path: 'library/tmp', confirm: true, expected_size_bytes: 2_400_000_000, expected_file_count: 80 })
  await expect(row(page, 'tmp')).toHaveCount(0)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash.getByTestId('trash-line')).toHaveText('Trash uses 2.4 GB; nothing is freed until you delete from it.')
  await expect(trash).toContainText('library/tmp')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/disk-usage-trashed-desktop.png`, fullPage: true })
})

test('Trash starts empty and says so', async ({ page }) => {
  const sec = await (async () => { await mockDiskUsage(page); return openSection(page) })()
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash.getByTestId('trash-line')).toHaveText('Trash uses 0 B; nothing is freed until you delete from it.')
  await expect(trash).toContainText('Trash is empty.')
  await expect(trash.getByRole('button', { name: 'Empty Trash…' })).toHaveCount(0)
})

test('restore puts an item back and the folder lists it again', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/scratch', size: 2_400_000_000, files: 80 }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash).toContainText('library/scratch')
  await trash.getByRole('button', { name: 'Restore library/scratch' }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Restored library/scratch to where it came from.' })).toBeVisible()
  expect(mock.posts[0]).toEqual({ path: 'restore', body: { id: mock.posts[0].body.id, confirm: true } })
  await expect(trash).toContainText('Trash is empty.')
  await sec.getByRole('button', { name: 'Open library' }).click()
  await expect(row(page, 'scratch')).toBeVisible()
})

test('a restore that is not possible shows the plain reason and keeps the item', async ({ page }) => {
  await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 5, files: 1, restorable: false }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash).toContainText('Restore may not work right now')
  await trash.getByRole('button', { name: 'Restore library/tmp' }).click()
  await expect(trash.getByRole('alert')).toContainText('Something with the same name is already in its old place.')
  await expect(trash).toContainText('library/tmp')
})

test('Delete permanently needs the typed word DELETE exactly and shows the size', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 2_400_000_000, files: 80 }, { path: 'model_cache/old', size: 1_000, files: 1 }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await trash.getByRole('button', { name: 'Delete library/tmp permanently' }).click()
  await expect(trash).toContainText('Permanently deletes library/tmp (2.4 GB · 80 files) from this PC.')
  const go = trash.getByRole('button', { name: 'Delete library/tmp permanently' }).last()
  const input = trash.getByLabel(/Type DELETE to confirm/)
  await expect(go).toBeDisabled()
  await input.fill('delete')
  await expect(go).toBeDisabled()
  await input.fill('DELETE')
  await expect(go).toBeEnabled()
  expect(mock.posts).toHaveLength(0)
  await go.click()
  await expect(sec.getByRole('status').filter({ hasText: 'Deleted library/tmp permanently. Freed 2.4 GB.' })).toBeVisible()
  expect(mock.posts[0].body).toMatchObject({ confirm_text: 'DELETE', expected_size_bytes: 2_400_000_000 })
  await expect(trash).not.toContainText('library/tmp')
  await expect(trash.getByTestId('trash-line')).toHaveText('Trash uses 1.0 KB; nothing is freed until you delete from it.')
})

test('Empty Trash needs the typed word and frees everything in it', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 2_400_000_000, files: 80 }, { path: 'model_cache/old', size: 600_000_000, files: 3 }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await trash.getByRole('button', { name: 'Empty Trash…' }).click()
  const go = trash.getByRole('button', { name: 'Empty Trash permanently' })
  await expect(go).toBeDisabled()
  await trash.getByLabel(/Type DELETE to confirm/).fill('DELETE')
  await go.click()
  await expect(sec.getByRole('status').filter({ hasText: 'Emptied Trash: 2 items deleted, 3.0 GB freed.' })).toBeVisible()
  expect(mock.posts[0]).toEqual({
    path: 'empty',
    body: { confirm_text: 'DELETE', expected_item_count: 2, expected_size_bytes: 3_000_000_000 },
  })
  await expect(trash).toContainText('Trash is empty.')
})

test('while a job runs, Trash can not be restored or deleted', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 5, files: 1 }] })
  mock.setBusy('A job, restore or other library task is running. Wait for it to finish, then try again.')
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash.getByRole('button', { name: 'Restore library/tmp' })).toBeDisabled()
  await expect(trash.getByRole('button', { name: 'Delete library/tmp permanently' })).toBeDisabled()
  await expect(trash.getByRole('button', { name: 'Empty Trash…' })).toBeDisabled()
})

test('title media needs the extra tick before Move to Trash works', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open dramas' }).click()
  const t7 = row(page, '7')
  const clear = t7.getByRole('button', { name: 'Move 7 to Trash' })
  await expect(clear).toBeDisabled()
  await t7.getByLabel(/I understand 7 is source media/).check()
  await expect(clear).toBeEnabled()
  await clear.click()
  await t7.getByRole('button', { name: /Confirm: move 7/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved 7' })).toBeVisible()
  expect(mock.posts[0].body.confirm_irreplaceable).toBe(true)
})

test('a changed item answers 409: the message shows and the list refreshes', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  mock.tree['library'].find((n) => n.name === 'tmp')!.size = 9
  await page.getByRole('button', { name: 'Move tmp to Trash' }).click()
  await page.getByRole('button', { name: /Confirm: move tmp/ }).click()
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
  const confirmBtn = auto.getByRole('button', { name: 'Move auto', exact: true })
  await expect(confirmBtn).toBeDisabled()
  await dest.fill('D:\\Baihe backups')
  await confirmBtn.click()
  await auto.getByRole('button', { name: /Confirm: move auto \(2\.9 GB\)/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved auto (2.9 GB). Baihe now uses the new folder.' })).toBeVisible()
  expect(mock.posts[0]).toEqual({ path: 'move', body: { path: 'library/backups/auto', destination: 'D:\\Baihe backups', confirm: true } })
})

test('busy library, partial scan and an empty folder are explained', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  mock.setBusy('A job, restore or other library task is running. Wait for it to finish, then try again.')
  mock.setPartial(true)
  const sec = await openSection(page)
  await expect(sec).toContainText('Counting took too long and was stopped.')
  await expect(sec).toContainText('A job, restore or other library task is running.')
  await expect(page.getByRole('button', { name: 'Move model_cache to Trash' })).toBeDisabled()
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open tmp' }).click()
  await expect(sec).toContainText('This folder is empty.')
})

test('backup copies need the can-not-be-recreated tick; a folder holding a link can not be cleared', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  await sec.getByRole('button', { name: 'Open backups' }).click()
  const auto = row(page, 'auto')
  await expect(auto).toContainText("Your backups. Once cleared they can't be recreated")
  await expect(auto.getByRole('button', { name: 'Move auto to Trash' })).toBeDisabled()
  await auto.getByLabel(/I understand auto holds backups/).check()
  await expect(auto.getByRole('button', { name: 'Move auto to Trash' })).toBeEnabled()
  const linked = row(page, 'linked')
  await expect(linked).toContainText('contains a link or junction')
  await expect(linked.getByRole('button', { name: 'Move linked to Trash' })).toBeDisabled()
  expect(mock.posts).toHaveLength(0)
})

test('a folder with more items than the list limit says how many are not shown', async ({ page }) => {
  await mockDiskUsage(page, { notShown: 345 })
  const sec = await openSection(page)
  await expect(sec).toContainText('345 more not shown')
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

test('keyboard: Tab reaches Open, Move to Trash and the confirm; Escape backs out', async ({ page }) => {
  const mock = await mockDiskUsage(page)
  const sec = await openSection(page)
  await sec.getByRole('button', { name: 'Open library' }).click()
  const tmp = row(page, 'tmp')
  await tmp.getByRole('button', { name: 'Move tmp to Trash' }).focus()
  await page.keyboard.press('Enter')
  const confirm = tmp.getByRole('button', { name: /Confirm: move tmp/ })
  await expect(confirm).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(tmp.getByRole('button', { name: 'Move tmp to Trash' })).toBeFocused()
  expect(mock.posts).toHaveLength(0)
})

test('the Trash list shows up while a slow scan is still running', async ({ page }) => {
  await mockDiskUsage(page, { slowScan: 4000, trash: [{ path: 'library/tmp', size: 5, files: 1 }] })
  const sec = await openSection(page)
  await expect(sec.getByRole('status').filter({ hasText: 'Scanning' })).toBeVisible()
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash).toContainText('library/tmp')
  await expect(sec.getByRole('status').filter({ hasText: 'Scanning' })).toBeVisible()
})

test('restoring or deleting an item that is already gone refreshes the list without an error', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 5, files: 1 }, { path: 'model_cache/old', size: 7, files: 1 }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash).toContainText('library/tmp')
  mock.trash.length = 0
  await trash.getByRole('button', { name: 'Restore library/tmp' }).click()
  await expect(trash).toContainText('Trash is empty.')
  await expect(trash.getByRole('alert')).toHaveCount(0)
})

test('Empty Trash after the list changed is refused and the list is reloaded', async ({ page }) => {
  const mock = await mockDiskUsage(page, { trash: [{ path: 'library/tmp', size: 5, files: 1 }] })
  const sec = await openSection(page)
  const trash = sec.getByRole('region', { name: 'Trash' })
  await expect(trash).toContainText('library/tmp')
  await trash.getByRole('button', { name: 'Empty Trash…' }).click()
  await expect(trash).toContainText('If anything in Trash changed since this list loaded, nothing is deleted.')
  mock.trash.push({ id: '20261003-100000-abcdef99', parent: 'model_cache', trashed_at: '2026-10-03T10:00:00+00:00', restorable: true,
    node: { name: 'old', kind: 'folder', size: 9, files: 1 } })
  await trash.getByLabel(/Type DELETE to confirm/).fill('DELETE')
  await trash.getByRole('button', { name: 'Empty Trash permanently' }).click()
  await expect(trash.getByRole('alert')).toContainText('The Trash changed since you looked.')
  await expect(trash).toContainText('model_cache/old')
  expect(mock.trash).toHaveLength(2)
})

// Unused voice clips: no file name or path is ever on screen; the server only sends type, size, date.
const CLIPS = [
  { title: 'Moonlit Court', clips: [{ id: 'a'.repeat(32), type: 'wav', size: 1_200_000 }, { id: 'b'.repeat(32), type: 'mp3', size: 300_000 }] },
  { title: 'Harbor Nights', clips: [{ id: 'c'.repeat(32), type: 'wav', size: 500_000 }] },
]

test('unused voice clips list per title with a total and no file names', async ({ page }) => {
  await mockDiskUsage(page, { clips: CLIPS })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await expect(clips).toContainText('Not used by any speaker. Moves to the Baihe trash, where you can restore it.')
  await expect(clips.getByTestId('clips-line')).toHaveText('3 clips · 2.0 MB')
  await expect(clips.locator('.du-clip-heading')).toHaveText(['Moonlit Court · 2 clips · 1.5 MB', 'Harbor Nights · 1 clip · 500.0 KB'])
  await expect(clips.locator('.du-clip-title').first().locator('.du-name').first()).toHaveText('WAV clip · 1.2 MB · 2026-09-30')
  await expect(clips).not.toContainText(/clone_|voice_refs|\.wav|\.mp3/)
})

test('moving one unused clip needs the second press and sends its id and shown size', async ({ page }) => {
  const mock = await mockDiskUsage(page, { clips: CLIPS })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await clips.getByRole('button', { name: 'Move clip 2 of Moonlit Court to Trash' }).click()
  expect(mock.posts.filter((p) => p.path === 'clips-to-trash')).toHaveLength(0)
  await clips.getByRole('button', { name: 'Confirm: move clip (300.0 KB) to Trash' }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved 1 clip (300.0 KB) to Trash' })).toBeVisible()
  expect(mock.posts.find((p) => p.path === 'clips-to-trash')!.body)
    .toEqual({ clips: [{ id: 'b'.repeat(32), expected_size_bytes: 300_000 }], confirm: true })
  await expect(clips.getByTestId('clips-line')).toHaveText('2 clips · 1.7 MB')
})

test('move all skips a clip a speaker started using and says so', async ({ page }) => {
  const mock = await mockDiskUsage(page, { clips: CLIPS })
  const sec = await openSection(page)
  mock.clips[1].clips[0].used = true
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await clips.getByRole('button', { name: 'Move all unused voice clips to Trash' }).click()
  await clips.getByRole('button', { name: 'Confirm: move 3 clips (2.0 MB) to Trash' }).click()
  await expect(sec.getByRole('status').filter({ hasText: '1 clip skipped: it changed or a speaker started using it.' })).toBeVisible()
  await expect(clips.getByTestId('clips-line')).toHaveText('1 clip · 500.0 KB')
})

const manyClips = (n: number) => [{
  title: 'Big Show', clips: Array.from({ length: n }, (_, i) => ({ id: i.toString(16).padStart(32, '0'), type: 'wav', size: 1 })),
}]

test('move all sends batches of at most 500 clips, one after another', async ({ page }) => {
  const mock = await mockDiskUsage(page, { clips: manyClips(1001) })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await clips.getByRole('button', { name: 'Move all unused voice clips to Trash' }).click()
  await clips.getByRole('button', { name: /^Confirm: move 1,001 clips/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved 1,001 clips' })).toBeVisible()
  const sizes = mock.posts.filter((p) => p.path === 'clips-to-trash').map((p) => (p.body.clips as unknown[]).length)
  expect(sizes).toEqual([500, 500, 1])
})

test('move all stops at the first failing batch and reports the totals', async ({ page }) => {
  const mock = await mockDiskUsage(page, { clips: manyClips(1001), clipsFailPost: 2 })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await clips.getByRole('button', { name: 'Move all unused voice clips to Trash' }).click()
  await clips.getByRole('button', { name: /^Confirm: move 1,001 clips/ }).click()
  await expect(sec.getByRole('status').filter({ hasText: 'Moved 502 of 1,001 clips' })).toContainText('stopped because A job, restore or other library task is running.')
  expect(mock.posts.filter((p) => p.path === 'clips-to-trash')).toHaveLength(2)
})

test('titles with a clip-reading job are called out, and an empty list says so', async ({ page }) => {
  await mockDiskUsage(page, { clips: [], clipsInUse: 2 })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await expect(clips).toContainText('No unused voice clips.')
  await expect(clips).toContainText('2 titles are left out because a dub, narration or audiobook job is running.')
})

test('unused voice clips on a phone: no sideways scroll, 44 px buttons', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await mockDiskUsage(page, { clips: CLIPS })
  const sec = await openSection(page)
  const clips = sec.getByRole('region', { name: 'Unused voice clips' })
  await expect(clips.getByRole('button', { name: 'Move all unused voice clips to Trash' })).toBeVisible()
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(wide).toBe(false)
  for (const b of await clips.getByRole('button', { name: /^Move (all|clip)/ }).all()) {
    expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/unused-clips-phone.png`, fullPage: true })
})
