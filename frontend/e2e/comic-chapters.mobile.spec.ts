import { expect, test, type Page } from '@playwright/test'

import { mockScanlate } from './scanlateMocks'

// Chapters in the comic viewer on a phone (390x844, touch): the selector row
// has 44 px targets and nothing scrolls sideways.

const CHAPTERS = [
  { title: 'Chapter 12', pages: 3 },
  { title: 'Chapter 13 — a rather long title from the source site', pages: 3 },
]
const bar = (page: Page) => page.getByTestId('comic-chapters')

test('phone: chapter row, divider, hidden toggle and scope fit the screen', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', chapters: CHAPTERS, hidden: [5], pageCount: 6 })
  await page.goto('/#/comic/7?page=1')
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 3 (1/5)')
  await expect(page.getByTestId('comic-divider')).toHaveText('Chapter 13 — a rather long title from the source site')

  const small = await bar(page).evaluate((root) =>
    [...root.querySelectorAll<HTMLElement>('button, select')]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, w: e.getBoundingClientRect().width, t: (e.getAttribute('aria-label') || e.textContent || '').trim() }))
      .filter((x) => x.h < 44 || x.w < 44))
  expect(small).toEqual([])
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll).toBeLessThanOrEqual(client)

  await bar(page).getByRole('button', { name: 'Show hidden (1)' }).click()
  await expect(page.getByText('Hidden: not part of the story')).toBeVisible()
  await bar(page).getByLabel('Chapter', { exact: true }).selectOption({ index: 1 })
  await expect(page).toHaveURL(/page=4$/)

  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const scope = page.getByLabel('Translate', { exact: true })
  await expect(scope.locator('option')).toHaveCount(2)
  expect(s.unmocked).toEqual([])
})
