import { expect, test } from '@playwright/test'

import { mockComic } from './comicMocks'

// Desktop layout breaks found in a sweep of every page.

test('Settings tabs keep every word whole', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 800 })
  await page.goto('/#/settings')
  await expect(page.getByRole('tab', { name: 'Costs' })).toBeVisible()
  const broken = await page.evaluate(() =>
    [...document.querySelectorAll('.ui-tab')].filter((tab) => {
      // One text rect per line: a single word split across lines gives two rects for one word.
      const words = (tab.textContent ?? '').trim().split(/\s+/)
      const range = document.createRange()
      range.selectNodeContents(tab)
      return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size > words.length
    }).map((t) => t.textContent),
  )
  expect(broken).toEqual([])
})

test('closed Settings cards are one row tall, not 60px taller', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('tab', { name: 'Performance and advanced' }).click()
  const heights = await page.evaluate(() =>
    [...document.querySelectorAll('.settings-panel:not([hidden]) > .settings-card')].slice(1).map((c) => c.getBoundingClientRect().height),
  )
  expect(heights.length).toBeGreaterThan(1)
  for (const h of heights) expect(h).toBeLessThan(70)
})

test('comic bar: no disabled Typeset, Text or zoom with no pages; two rows with pages', async ({ page }) => {
  await mockComic(page, { pageCount: 0 })
  await page.goto('/#/comic/7')
  await expect(page.locator('.comic-top')).toBeVisible()
  await expect(page.getByRole('group', { name: 'Show' })).toHaveCount(0)

  await mockComic(page, {
    mediaType: 'manhua', pageCount: 8, sourceUrl: 'https://example.com/a/b', title: 'A fairly long comic title for the toolbar',
    chapters: [{ title: 'Ch 1', pages: 4 }, { title: 'Ch 2', pages: 4 }],
  })
  await page.goto('/#/comic/8')
  await expect(page.getByRole('group', { name: 'Show' })).toBeVisible()
  // A row of controls is about 44px; the chapter row is the second, and a third row took the bar past 170px.
  const height = await page.locator('.comic-top').evaluate((bar) => bar.getBoundingClientRect().height)
  expect(height).toBeLessThan(140)
})
