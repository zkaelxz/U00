import { expect, test } from '@playwright/test'
import { openCreditsTab, openFillIn } from './source-groups'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone (390x844): Edit details' credits and cover, the known platforms list and the EPUB
// chapter range fit the width.

test('Source preamble panels fit a phone', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await openCreditsTab(page, true)
  await expect(page.getByRole('button', { name: 'Romanize credits' })).toBeVisible()
  await openFillIn(page, 'From a page or text')
  await page.getByText('Known official platforms', { exact: true }).click()
  await expect(page.getByRole('list', { name: 'Known official platforms' })).toBeVisible()
  const link = page.getByRole('list', { name: 'Known official platforms' }).getByRole('link').first()
  expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expectNoSidewaysScroll(page)
})
