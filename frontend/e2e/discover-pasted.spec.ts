import { expect, test, type Page } from '@playwright/test'

import { mockDiscover, openTab, posts } from './discoverMocks'

// Desktop: Discover bulk import's manual fallback (DI07): paste the listing
// text a plain fetch couldn't read; the same review and add follow.

const openSection = async (page: Page, title: string) => {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) })
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

test('pasted listing text: extract, review, add', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  await openTab(page, 'Add titles')
  await openSection(page, 'Bulk import from listing pages')
  await openSection(page, 'Paste the listing text instead')
  const go = page.getByRole('button', { name: 'Extract from pasted text' })
  await expect(go).toBeDisabled()
  await page.getByRole('textbox', { name: 'Pasted listing text' }).fill('排行榜\n1. 长公主 A\n2. 青梅 B')
  await go.click()
  await expect(page.getByText(/Read 1 of 2 pages/)).toBeVisible()
  expect(posts(s, '/bulk-extract/pasted')[0].body).toEqual({
    text: '排行榜\n1. 长公主 A\n2. 青梅 B', source_label: 'jjwxc_baihe_tag', engine: 'claude',
  })
  s.bulk = 'done'
  const review = page.getByTestId('bulk-review')
  await expect(review.getByText('Review 3 entries')).toBeVisible()
  await review.getByRole('button', { name: 'Add 3 to catalogue' }).click()
  await expect(review.getByText(/Added 2 titles to your catalogue/)).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('pasted listing: too long is refused before sending', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  await openTab(page, 'Add titles')
  await openSection(page, 'Bulk import from listing pages')
  await openSection(page, 'Paste the listing text instead')
  await page.getByRole('textbox', { name: 'Pasted listing text' }).fill('x'.repeat(200_001))
  await expect(page.getByText(/more than 200,000 characters/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Extract from pasted text' })).toBeDisabled()
  expect(posts(s, '/bulk-extract/pasted')).toHaveLength(0)
})
