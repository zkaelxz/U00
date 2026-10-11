import { expect, test } from '@playwright/test'

import { asMediaType, exportBlock, mockExportReady, openExportBlock } from './exportBlocks'
import { BLOCKS, FIRST_OPEN, expectOpen, mockSoftsubRun, summaryOf } from './exportBlocksCases'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await mockExportReady(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const noSideScroll = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

test('headings are 44px tall, each kind opens its own block and nothing scrolls sideways', async ({ page }) => {
  await page.goto('/#/drama/3/export')
  await expectOpen(page, { [FIRST_OPEN.video]: true, Audiobook: false, 'Subtitle file': false })
  for (const title of BLOCKS) expect(await hitHeight(summaryOf(page, title))).toBeGreaterThanOrEqual(44)
  expect(await noSideScroll(page)).toBeLessThanOrEqual(0)

  await page.goto('/#/drama/1/export')
  await expectOpen(page, { [FIRST_OPEN.audio]: true, 'Video with a subtitle track': false })
  await page.goto('/#/drama/2/export')
  await expectOpen(page, { [FIRST_OPEN.novel]: true, Audiobook: false })
  await asMediaType(page, 'other')
  await page.goto('/#/drama/1/export')
  await expectOpen(page, { [FIRST_OPEN.subtitle]: true, Audiobook: false })
  expect(await noSideScroll(page)).toBeLessThanOrEqual(0)
})

test('a tap folds a block, the choice survives a reload, and a running job stays visible', async ({ page }) => {
  const { finish } = await mockSoftsubRun(page, 3)
  await page.goto('/#/drama/3/export')
  const group = exportBlock(page, 'Video with a subtitle track')
  await group.getByRole('button', { name: 'Start subtitle-track video export' }).click()
  await expect(group.getByTestId('job-status')).toContainText('Running')
  await summaryOf(page, 'Audiobook').tap()
  await expectOpen(page, { Audiobook: true, 'Video with a subtitle track': false })
  await expect(group.getByTestId('job-status')).toContainText('Running')
  finish()
  await expect(page.getByTestId('artifact-softsub').getByRole('link')).toHaveText('Download softsub_video_1.mkv')
  await page.reload()
  await expectOpen(page, { Audiobook: true, 'Video with a subtitle track': false })
})

test('unavailable exports are disabled with a one-line reason and fit the width', async ({ page }) => {
  await mockExportReady(page, { hasSourceVideo: false })
  await page.goto('/#/drama/3/export')
  await expect(summaryOf(page, 'Burned-in video')).toContainText('No source video yet. Add one in Media.')
  await openExportBlock(page, 'Video with a subtitle track')
  await expect(exportBlock(page, 'Video with a subtitle track').getByRole('button', { name: 'Start subtitle-track video export' })).toBeDisabled()
  expect(await noSideScroll(page)).toBeLessThanOrEqual(0)
})
