import { expect, test, type Page } from '@playwright/test'

import { mockBackups } from './backupsMocks'

// Automatic backups (Step 43), desktop. The settings test runs against the
// real seeded API (a throwaway library) and resets what it changed; the
// Back up now and copies tests use the stateful mocks in backupsMocks.ts.
// BACKUP_SHOTS_DIR=<dir> also saves review screenshots.

const SHOTS = process.env.BACKUP_SHOTS_DIR

const card = (page: Page) => page.getByRole('region', { name: 'Automatic backups', exact: true })

async function openAdmin(page: Page) {
  await page.goto('/')
  const summary = page.locator('summary', { hasText: 'Backup & storage' })
  const details = page.locator('details.section', { has: summary })
  if ((await details.getAttribute('open')) === null) await summary.click()
  return page.getByTestId('snapshot-block')
}

test.describe('Settings card (real API)', () => {
  test.afterAll(async ({ request }) => {
    await request.post('/api/backups/settings', { data: { enabled: false, frequency: 'daily', folder: '' } })
  })

  test('the toggle and frequency persist; a bad folder is refused in plain words', async ({ page }) => {
    await page.goto('/#/settings')
    const c = card(page)
    await expect(c).toContainText('Keeps one copy per day for the last 2 days, plus the first copy of each of the last 2 weeks')
    const auto = c.getByRole('switch', { name: 'Back up automatically' })
    await expect(auto).not.toBeChecked()
    // Daily is the default.
    await expect(c.getByRole('radio', { name: 'Daily' })).toBeChecked()
    await expect(c.getByRole('switch', { name: /Include media/ })).not.toBeChecked()

    const saved = page.waitForResponse((r) => r.url().endsWith('/api/backups/settings') && r.request().method() === 'POST')
    await auto.click()
    expect((await saved).request().postDataJSON()).toEqual({ enabled: true })
    await expect(auto).toBeChecked()
    await expect(c).toContainText(/Next backup:/)

    const freq = page.waitForResponse((r) => r.url().endsWith('/api/backups/settings') && r.request().method() === 'POST')
    await c.getByRole('radio', { name: 'Weekly' }).check()
    expect((await freq).request().postDataJSON()).toEqual({ frequency: 'weekly' })

    await page.reload()
    await expect(card(page).getByRole('switch', { name: 'Back up automatically' })).toBeChecked()
    await expect(card(page).getByRole('radio', { name: 'Weekly' })).toBeChecked()
    await expect(card(page).locator('.card-meta')).toHaveText('Weekly · database only')

    const folder = card(page).getByRole('textbox', { name: 'Backup folder' })
    await expect(folder).toHaveAttribute('placeholder', 'Library backups folder (default)')
    await folder.fill('relative/folder')
    await folder.blur()
    // The server's folder message carries no path, so it is shown as is.
    await expect(card(page).getByRole('alert')).toHaveText(
      'The backup folder must be a full folder path, starting with the drive letter.',
    )
  })
})

test.describe('Back up now and the copies (mocked)', () => {
  test('lists the copies newest first; Back up now adds one at once, no replace question', async ({ page }) => {
    const state = await mockBackups(page, { jobPollsBeforeDone: 2 })
    await page.goto('/#/settings')
    const c = card(page)
    await expect(c.getByTestId('auto-backup-snapshot')).toContainText('Database only · 12.3 MB · 3 dramas')
    const copies = c.getByRole('list', { name: 'Backup copies, newest first' }).getByRole('listitem')
    await expect(copies).toHaveCount(3)
    await expect(copies.nth(0)).toContainText(/2026.* · Database only · 12.3 MB · daily$/)
    await expect(copies.nth(2)).toContainText(/Database \+ media · 11.0 MB · weekly$/)
    if (SHOTS) {
      await page.setViewportSize({ width: 1440, height: 900 })
      await c.scrollIntoViewIfNeeded()
      await c.screenshot({ path: `${SHOTS}/settings-auto-backups-desktop.png` })
      await page.screenshot({ path: `${SHOTS}/settings-page-1440.png`, fullPage: true })
    }

    await c.getByRole('button', { name: 'Back up now' }).click()
    await expect(c.getByText('Backup finished.')).toBeVisible()
    expect(state.posts).toEqual([{ path: '/api/backups/now', body: {} }])
    await expect(copies).toHaveCount(4)
    await expect(copies.nth(0)).toContainText(/12.4 MB · daily$/)
    await expect(c).toContainText(/Last backup: .*2026/)
  })

  test('with no copies, Back up now starts at once', async ({ page }) => {
    const state = await mockBackups(page, { snapshot: { exists: false, copies: [] } })
    await page.goto('/#/settings')
    await expect(card(page).getByTestId('auto-backup-snapshot')).toHaveText('Newest copy: No snapshot yet.')
    await expect(card(page).getByTestId('auto-backup-copies')).toHaveCount(0)
    await card(page).getByRole('button', { name: 'Back up now' }).click()
    await expect(card(page).getByText('Backup finished.')).toBeVisible()
    expect(state.posts).toEqual([{ path: '/api/backups/now', body: {} }])
  })

  test('Library tools restores from a chosen older copy', async ({ page }) => {
    const state = await mockBackups(page)
    const block = await openAdmin(page)
    await block.getByRole('button', { name: 'Restore one drama…' }).click()
    const from = block.getByLabel('Restore from')
    await expect(from).toHaveValue('baihe_snapshot-20260928-093000.zip')
    await expect(block.getByRole('list', { name: 'Dramas in the copy' }).getByRole('button')).toHaveCount(3)
    await from.selectOption('baihe_snapshot-20260921-093000.zip')
    const list = block.getByRole('list', { name: 'Dramas in the copy' })
    await expect(list.getByRole('button')).toHaveCount(2)
    await list.getByRole('button', { name: /Signal/ }).click()
    await block.getByLabel(/Type RESTORE to confirm/).fill('RESTORE')
    await block.getByRole('button', { name: 'Restore drama' }).click()
    await expect(block.getByTestId('restore-result')).toContainText("Restored 'Signal'.")
    expect(state.posts).toEqual([{
      path: '/api/backups/snapshot/restore-drama',
      body: { drama_id: 3, confirm: true, confirm_text: 'RESTORE', snapshot: 'baihe_snapshot-20260921-093000.zip' },
    }])
  })

  test('Library tools deletes one chosen copy, then all of them, each with a typed DELETE', async ({ page }) => {
    const state = await mockBackups(page)
    const block = await openAdmin(page)
    await expect(block.getByTestId('snapshot-info')).toContainText('Database only · 12.3 MB · 3 dramas')
    if (SHOTS) {
      await page.setViewportSize({ width: 1440, height: 900 })
      await block.screenshot({ path: `${SHOTS}/library-snapshot-desktop.png` })
      await page.screenshot({ path: `${SHOTS}/library-page-1440.png`, fullPage: true })
    }

    await block.getByRole('button', { name: 'Delete a copy…' }).click()
    // The oldest copy is chosen first.
    await expect(block.getByLabel('Copy to delete')).toHaveValue('baihe_snapshot-20260921-093000.zip')
    const go = block.getByRole('button', { name: 'Delete copy', exact: true })
    await block.getByLabel(/Type DELETE to confirm/).fill('delete')
    await expect(go).toBeDisabled()
    await block.getByLabel(/Type DELETE to confirm/).fill('DELETE')
    await go.click()
    await expect(block.getByText('Copy deleted.')).toBeVisible()
    await expect(block.getByTestId('snapshot-info')).toContainText('Database only · 12.3 MB · 3 dramas')

    await block.getByRole('button', { name: 'Delete a copy…' }).click()
    await block.getByLabel('Copy to delete').selectOption({ label: 'All copies (2)' })
    await block.getByLabel(/Type DELETE to confirm/).fill('DELETE')
    await block.getByRole('button', { name: 'Delete all copies', exact: true }).click()
    await expect(block.getByText('All copies deleted.')).toBeVisible()
    await expect(block.getByTestId('snapshot-info')).toHaveText('No snapshot yet.')
    await expect(block.getByRole('button', { name: 'Restore one drama…' })).toBeDisabled()
    expect(state.posts).toEqual([
      { path: '/api/backups/snapshot/delete', body: { confirm: true, confirm_text: 'DELETE', snapshot: 'baihe_snapshot-20260921-093000.zip' } },
      { path: '/api/backups/snapshot/delete', body: { confirm: true, confirm_text: 'DELETE', all: true } },
    ])
  })
})
