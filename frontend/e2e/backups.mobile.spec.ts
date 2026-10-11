import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockBackups } from './backupsMocks'
import { openSettingsGroups } from './settingsNav'

// Phone project (390x844, touch): the Settings "Automatic backups" Card and
// restoring one drama from a backup copy in Library tools, on the stateful
// mocks in backupsMocks.ts. BACKUP_SHOTS_DIR=<dir> also saves screenshots.

const SHOTS = process.env.BACKUP_SHOTS_DIR

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tallEnough(scope: Locator, selector: string) {
  const boxes = await scope.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) })))
  expect(boxes.length).toBeGreaterThan(0)
  for (const { h, text } of boxes) expect(h, text).toBeGreaterThanOrEqual(44)
}

test('Settings card fits a phone with 44px targets', async ({ page }) => {
  await mockBackups(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const card = page.getByRole('region', { name: 'Automatic backups', exact: true })
  await expect(card.getByTestId('auto-backup-snapshot')).toContainText('Database only')
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('list', { name: 'Backup copies, newest first' }).getByRole('listitem')).toHaveCount(3)
  await tallEnough(card, 'button:not(.field-help-btn, .toggle), input, .segmented label')
  // The Toggle's hit area is its ::after (44px), not its box.
  await expect(card.getByRole('switch', { name: 'Back up automatically' })).toBeVisible()
  await noSideways(page)
  if (SHOTS) await card.screenshot({ path: `${SHOTS}/settings-auto-backups-phone.png` })
  await card.getByRole('button', { name: 'Back up now' }).click()
  await expect(card.getByText('Backup finished.')).toBeVisible()
  await noSideways(page)
})

test('restore one title from the newest copy on a phone', async ({ page }) => {
  const state = await mockBackups(page)
  await page.goto('/#/library-tools')
  const summary = page.locator('summary', { hasText: 'Backup & storage' })
  const details = page.locator('details.section', { has: summary })
  if ((await details.getAttribute('open')) === null) await summary.click()
  const block = page.getByTestId('snapshot-block')
  await expect(block.getByTestId('snapshot-info')).toContainText('Database only · 12.3 MB · 3 titles')
  await block.scrollIntoViewIfNeeded()
  if (SHOTS) await block.screenshot({ path: `${SHOTS}/library-snapshot-phone.png` })

  await block.getByRole('button', { name: 'Restore one title…' }).click()
  await expect(block.getByLabel('Restore from')).toHaveValue('baihe_snapshot-20260928-093000.zip')
  const list = block.getByRole('list', { name: 'Titles in the copy' })
  await expect(list.getByRole('button')).toHaveCount(3)
  await expect(list).toContainText('still in your library')
  await tallEnough(list, 'button')
  await noSideways(page)
  if (SHOTS) await block.screenshot({ path: `${SHOTS}/library-restore-list-phone.png` })

  await list.getByRole('button', { name: /Grandmaster of Demonic Cultivation/ }).click()
  const notes = block.getByTestId('restore-notes')
  await expect(notes).toContainText("Still in your library — it will be restored as a new copy titled 'Grandmaster of Demonic Cultivation (restored ")
  await expect(notes).toContainText('Files (audio/video/pages) are not in this snapshot; only the text and settings come back.')
  const go = block.getByRole('button', { name: 'Restore title' })
  await expect(go).toBeDisabled()
  await block.getByLabel(/Type RESTORE to confirm/).fill('RESTORE')
  await tallEnough(block, 'button:not(.link), input')
  await noSideways(page)
  if (SHOTS) await block.screenshot({ path: `${SHOTS}/library-restore-confirm-phone.png` })
  await go.click()

  const result = block.getByTestId('restore-result')
  await expect(result).toContainText("Restored 'Grandmaster of Demonic Cultivation (restored 2026-09-30)' as a new title. 812 lines.")
  await expect(result.getByRole('link', { name: 'Open title' })).toHaveAttribute('href', '#/drama/40')
  expect(state.posts).toEqual([
    {
      path: '/api/backups/snapshot/restore-drama',
      body: { drama_id: 1, confirm: true, confirm_text: 'RESTORE', snapshot: 'baihe_snapshot-20260928-093000.zip' },
    },
  ])
  await noSideways(page)
})

test('import from a backup file fits a phone with 44px targets', async ({ page }) => {
  await mockBackups(page)
  await page.goto('/#/library-tools')
  const summary = page.locator('summary', { hasText: 'Backup & storage' })
  const details = page.locator('details.section', { has: summary })
  if ((await details.getAttribute('open')) === null) await summary.click()
  const block = page.getByTestId('snapshot-block')
  await block.scrollIntoViewIfNeeded()
  await block.getByRole('button', { name: 'From a backup file…' }).click()
  await block.getByLabel(/Backup file/).setInputFiles({ name: 'manual.zip', mimeType: 'application/zip', buffer: Buffer.from('zip') })
  const list = block.getByRole('list', { name: 'Titles in the file' })
  await expect(list.getByRole('checkbox')).toHaveCount(2)
  await tallEnough(list, 'label')
  await list.getByRole('checkbox', { name: /Second title/ }).check()
  await tallEnough(block, 'button:not(.link)')
  await noSideways(page)
  if (SHOTS) await block.screenshot({ path: `${SHOTS}/library-import-phone.png` })
  await block.getByRole('button', { name: /^Import/ }).click()
  await block.getByLabel(/Type RESTORE to confirm/).fill('RESTORE')
  await noSideways(page)
  await block.getByRole('button', { name: 'Import titles' }).click()
  await expect(block.getByTestId('import-result')).toContainText('Imported 1 title.')
  await noSideways(page)
})
