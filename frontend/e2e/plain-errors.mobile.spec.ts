import { expect, test } from '@playwright/test'

import { hitHeight, installHitArea } from './hitArea'
import { mockProgress } from './plainErrorsMocks'
import { exportBlock, mockExportReady, openExportBlock } from './exportBlocks'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// Phone: the disabled-with-reason states fit the width without sideways scroll.

const noSideScroll = () => document.documentElement.scrollWidth <= window.innerWidth

test('phone: Export buttons are disabled with their reason and nothing scrolls sideways', async ({ page }) => {
  await mockProgress(page)
  await mockExportReady(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  const audiobook = exportBlock(page, 'Audiobook')
  await expect(audiobook.getByRole('button', { name: 'Start audiobook export' })).toBeDisabled()
  await expect(audiobook).toContainText('There is no narration yet. Create it with Listen in the Reader first.')
  await expect(page.getByRole('group', { name: 'Video with the dub audio' })).toContainText('There is no dub track for this title.')
  expect(await page.evaluate(noSideScroll)).toBe(true)
})
