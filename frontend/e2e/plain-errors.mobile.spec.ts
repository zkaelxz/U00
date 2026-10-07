import { expect, test } from '@playwright/test'

import { hitHeight, installHitArea } from './hitArea'
import { mockDub, mockProgress } from './plainErrorsMocks'
import { openExportBlocks } from './exportBlocks'

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
  await page.goto('/#/drama/1/export')
  await page.getByText('Video and audio', { exact: true }).click()
  await openExportBlocks(page)
  const audiobook = page.getByRole('group', { name: 'Audiobook' })
  await expect(audiobook.getByRole('button', { name: 'Start audiobook export' })).toBeDisabled()
  await expect(audiobook).toContainText('There is no narration yet. Create it in Dub first.')
  await expect(page.getByRole('group', { name: 'Video with the dub audio' })).toContainText('There is no dub yet.')
  expect(await page.evaluate(noSideScroll)).toBe(true)
})

test('phone: Generate dub is disabled with the missing-package reason', async ({ page }) => {
  await mockDub(page, 'OmniVoice is not installed. Install it in Diagnostics.')
  await page.goto('/#/drama/1/dub')
  const generate = page.getByRole('button', { name: 'Generate dub' })
  await expect(generate).toBeDisabled()
  expect(await hitHeight(generate)).toBeGreaterThanOrEqual(44)
  await expect(page.getByTestId('dub-settings')).toContainText('OmniVoice is not installed. Install it in Diagnostics.')
  expect(await page.evaluate(noSideScroll)).toBe(true)
})
