import { expect, test } from '@playwright/test'

import { exportBlock, mockExportReady, openExportBlock } from './exportBlocks'
import { selectMediaTab } from './mediaTabs'
import { mockProgress } from './plainErrorsMocks'

// Plain reasons instead of generic or raw errors: Export buttons that
// cannot work yet are disabled with the reason; refusals read as sentences.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test.beforeEach(async ({ page }) => {
  await mockExportReady(page)
})

test('audiobook and dubbed video are disabled with the reason when there is no narration or dub', async ({ page }) => {
  await mockProgress(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  const audiobook = exportBlock(page, 'Audiobook')
  await expect(audiobook.getByRole('button', { name: 'Start audiobook export' })).toBeDisabled()
  await expect(audiobook).toContainText('There is no narration yet. Create it with Listen in the Reader first.')
  await openExportBlock(page, 'Video with the dub audio')
  const dubbed = exportBlock(page, 'Video with the dub audio')
  await expect(dubbed.getByRole('button', { name: 'Start dubbed video export' })).toBeDisabled()
  await expect(dubbed).toContainText('There is no dub track for this title.')
})

test('with a narration and a dub the buttons are enabled', async ({ page }) => {
  await mockProgress(page, { has_dub_track: true, has_narration_track: true })
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  await expect(page.getByRole('button', { name: 'Start audiobook export' })).toBeEnabled()
  await openExportBlock(page, 'Video with the dub audio')
  await expect(page.getByRole('button', { name: 'Start dubbed video export' })).toBeEnabled()
})

test('a refused start shows the server sentence once, not the generic text', async ({ page }) => {
  await mockProgress(page, { has_narration_track: true })
  await page.route('**/api/export/dramas/1/audiobook', (route) =>
    route.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'There is no narration yet. Create it with Listen in the Reader first.' } } }),
  )
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  const start = page.getByRole('button', { name: 'Start audiobook export' })
  await start.click()
  await start.click()
  const alerts = page.getByRole('alert').filter({ hasText: 'There is no narration yet' })
  await expect(alerts).toHaveCount(1)
  await expect(page.getByText('Some of the values entered are not valid')).toHaveCount(0)
})

test('the soft-subtitle help says anything else becomes MKV', async ({ page }) => {
  await mockProgress(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Video with a subtitle track')
  const group = exportBlock(page, 'Video with a subtitle track')
  await expect(group).toContainText('MP4 and MKV keep their format, anything else becomes MKV.')
})
