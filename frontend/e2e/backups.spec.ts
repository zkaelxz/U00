import { expect, test, type Page } from '@playwright/test'

import { mockBackups } from './backupsMocks'

// Automatic backups (Step 43), desktop. The settings test runs against the
// real seeded API (a throwaway library) and resets what it changed; the
// Back up now and snapshot tests use the stateful mocks in backupsMocks.ts.
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
    await request.post('/api/backups/settings', { data: { enabled: false, frequency: 'weekly', folder: '' } })
  })

  test('the toggle and frequency persist; a bad folder is refused in plain words', async ({ page }) => {
    await page.goto('/#/settings')
    const c = card(page)
    await expect(c).toContainText('Keeps one snapshot; each new backup replaces it.')
    const auto = c.getByRole('switch', { name: 'Back up automatically' })
    await expect(auto).not.toBeChecked()
    await expect(c.getByRole('radio', { name: 'Weekly' })).toBeChecked()
    await expect(c.getByRole('switch', { name: /Include media/ })).not.toBeChecked()

    const saved = page.waitForResponse((r) => r.url().endsWith('/api/backups/settings') && r.request().method() === 'POST')
    await auto.click()
    expect((await saved).request().postDataJSON()).toEqual({ enabled: true })
    await expect(auto).toBeChecked()
    await expect(c).toContainText(/Next backup:/)

    const freq = page.waitForResponse((r) => r.url().endsWith('/api/backups/settings') && r.request().method() === 'POST')
    await c.getByRole('radio', { name: 'Daily' }).check()
    expect((await freq).request().postDataJSON()).toEqual({ frequency: 'daily' })

    await page.reload()
    await expect(card(page).getByRole('switch', { name: 'Back up automatically' })).toBeChecked()
    await expect(card(page).getByRole('radio', { name: 'Daily' })).toBeChecked()
    await expect(card(page).locator('.card-meta')).toHaveText('Daily · database only')

    const folder = card(page).getByRole('textbox', { name: 'Backup folder' })
    await expect(folder).toHaveAttribute('placeholder', 'Library backups folder (default)')
    await folder.fill('relative/folder')
    await folder.blur()
    // The server's sentence names an example path, so the field shows its own rules instead.
    await expect(card(page).getByRole('alert')).toHaveText(
      'Use a full folder path that already exists and is outside the library (for example D:\\Backups), or leave it empty.',
    )
  })
})

test.describe('Back up now and the snapshot (mocked)', () => {
  test('with a snapshot, Back up now asks to replace it first, then sends replace', async ({ page }) => {
    const state = await mockBackups(page, { jobPollsBeforeDone: 2 })
    await page.goto('/#/settings')
    const c = card(page)
    await expect(c.getByTestId('auto-backup-snapshot')).toContainText('Database only · 12.3 MB · 3 dramas')
    if (SHOTS) {
      await page.setViewportSize({ width: 1440, height: 900 })
      await c.scrollIntoViewIfNeeded()
      await c.screenshot({ path: `${SHOTS}/settings-auto-backups-desktop.png` })
      await page.screenshot({ path: `${SHOTS}/settings-page-1440.png`, fullPage: true })
    }

    await c.getByRole('button', { name: 'Back up now' }).click()
    const confirm = c.getByRole('group', { name: 'Replace the snapshot?' })
    await expect(confirm).toContainText(/This replaces the snapshot from .*2026/)
    await expect(confirm).toContainText('(Database only · 12.3 MB · 3 dramas). Only one snapshot is kept.')
    expect(state.posts).toEqual([])
    if (SHOTS) await c.screenshot({ path: `${SHOTS}/settings-auto-backups-replace-desktop.png` })

    // Cancel keeps it; asking again and confirming sends replace: true.
    await confirm.getByRole('button', { name: 'Cancel' }).click()
    await expect(confirm).toHaveCount(0)
    await c.getByRole('button', { name: 'Back up now' }).click()
    await c.getByRole('button', { name: 'Replace snapshot' }).click()
    await expect(c.getByText('Backup finished.')).toBeVisible()
    expect(state.posts).toEqual([{ path: '/api/backups/now', body: { replace: true } }])
    await expect(c).toContainText(/Last backup: .*2026/)
  })

  test('with no snapshot, Back up now starts at once', async ({ page }) => {
    const state = await mockBackups(page, { snapshot: { exists: false } })
    await page.goto('/#/settings')
    await expect(card(page).getByTestId('auto-backup-snapshot')).toHaveText('Snapshot: No snapshot yet.')
    await card(page).getByRole('button', { name: 'Back up now' }).click()
    await expect(card(page).getByText('Backup finished.')).toBeVisible()
    expect(state.posts).toEqual([{ path: '/api/backups/now', body: { replace: false } }])
  })

  test('Library tools shows the snapshot and deletes it with a typed DELETE', async ({ page }) => {
    const state = await mockBackups(page)
    const block = await openAdmin(page)
    await expect(block.getByTestId('snapshot-info')).toContainText('Database only · 12.3 MB · 3 dramas')
    if (SHOTS) {
      await page.setViewportSize({ width: 1440, height: 900 })
      await block.screenshot({ path: `${SHOTS}/library-snapshot-desktop.png` })
      await page.screenshot({ path: `${SHOTS}/library-page-1440.png`, fullPage: true })
    }

    await block.getByRole('button', { name: 'Delete snapshot…' }).click()
    const go = block.getByRole('button', { name: 'Delete snapshot', exact: true })
    await block.getByLabel(/Type DELETE to confirm/).fill('delete')
    await expect(go).toBeDisabled()
    await block.getByLabel(/Type DELETE to confirm/).fill('DELETE')
    await go.click()
    await expect(block.getByText('Snapshot deleted.')).toBeVisible()
    await expect(block.getByTestId('snapshot-info')).toHaveText('No snapshot yet.')
    await expect(block.getByRole('button', { name: 'Restore one drama…' })).toBeDisabled()
    expect(state.posts).toEqual([{ path: '/api/backups/snapshot/delete', body: { confirm: true, confirm_text: 'DELETE' } }])
  })
})
