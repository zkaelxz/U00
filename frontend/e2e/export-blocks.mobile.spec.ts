import { expect, test } from '@playwright/test'

import { BLOCKS, DEFAULT_OPEN, expectExpanded, mockSoftsubRun, toggle } from './exportBlocksCases'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const openMedia = (page: import('@playwright/test').Page) => page.getByText('Video and audio', { exact: true }).click()

test('headings are 44px tall, default state holds and nothing scrolls sideways', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  await expectExpanded(page, DEFAULT_OPEN)
  for (const title of BLOCKS) expect(await hitHeight(toggle(page, title))).toBeGreaterThanOrEqual(44)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('a tap folds a block, the choice survives a reload, and a running job stays visible', async ({ page }) => {
  const { finish } = await mockSoftsubRun(page)
  await page.goto('/#/drama/1/export')
  await openMedia(page)
  const group = page.getByRole('group', { name: 'Video with a subtitle track' })
  await group.getByRole('button', { name: 'Start subtitle-track video export' }).click()
  await expect(group.getByTestId('job-status')).toContainText('Running')
  await toggle(page, 'Video with a subtitle track').tap()
  await expect(toggle(page, 'Video with a subtitle track')).toHaveAttribute('aria-expanded', 'false')
  await expect(group.getByTestId('job-status')).toContainText('Running')
  await toggle(page, 'Audiobook').tap()
  finish()
  await expect(page.getByTestId('artifact-softsub').getByRole('link')).toHaveText('Download softsub_video_1.mkv')
  await page.reload()
  await expectExpanded(page, { ...DEFAULT_OPEN, 'Video with a subtitle track': false, Audiobook: true })
})
