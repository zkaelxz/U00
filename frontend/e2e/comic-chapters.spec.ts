import { expect, test, type Page } from '@playwright/test'

import { mockScanlate } from './scanlateMocks'

// Chapters in the comic viewer at the desktop viewport. Every /api call is
// mocked (comicMocks + scanlateMocks); nothing may fall through.

const CHAPTERS = [
  { title: 'Chapter 1', pages: 3 },
  { title: 'Chapter 2', pages: 2 },
  { title: 'Chapter 3', pages: 3 },
]
const bar = (page: Page) => page.getByTestId('comic-chapters')
const figures = (page: Page) => page.getByTestId('comic-stage').locator('figure')

test('chapter selector lists counts, jumps, and shows the relative and overall page', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', chapters: CHAPTERS })
  await page.goto('/#/comic/7?page=1')
  const select = bar(page).getByLabel('Chapter', { exact: true })
  await expect(select.locator('option')).toHaveText(['Chapter 1 (3 pages)', 'Chapter 2 (2 pages)', 'Chapter 3 (3 pages)'])
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 3 (1/8)')

  await select.selectOption({ label: 'Chapter 3 (3 pages)' })
  await expect(page).toHaveURL(/page=6$/)
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 3 (6/8)')
  await bar(page).getByRole('button', { name: 'Previous chapter' }).click()
  await expect(page).toHaveURL(/page=4$/)
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 2 (4/8)')
  await bar(page).getByRole('button', { name: 'Next chapter' }).click()
  await expect(page).toHaveURL(/page=6$/)
  await expect(bar(page).getByRole('button', { name: 'Next chapter' })).toBeDisabled()
  expect(s.unmocked).toEqual([])
})

test('reading continues across chapters with a divider between them', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', chapters: CHAPTERS })
  await page.goto('/#/comic/7?page=1')
  await expect(figures(page)).toHaveCount(8)
  await expect(page.getByTestId('comic-divider')).toHaveText(['Chapter 2', 'Chapter 3'])
  expect(s.unmocked).toEqual([])
})

test('read only this chapter keeps the reader inside it', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manga', chapters: CHAPTERS })
  await page.goto('/#/comic/7?page=4')
  await bar(page).getByRole('button', { name: 'Chapters and pages' }).click()
  await page.getByRole('checkbox', { name: 'Read only this chapter' }).check()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('comic-page-label')).toHaveText('Page 1 of 2')
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 2 of 2 (5/8)')
  await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled()
  expect(s.unmocked).toEqual([])
})

test('hidden pages are left out, shown on request, and can be hidden and restored', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', chapters: CHAPTERS, hidden: [2, 7] })
  await page.goto('/#/comic/7?page=1')
  await expect(figures(page)).toHaveCount(6)
  const select = bar(page).getByLabel('Chapter', { exact: true })
  await expect(select.locator('option')).toHaveText(['Chapter 1 (2 pages)', 'Chapter 2 (2 pages)', 'Chapter 3 (2 pages)'])

  await bar(page).getByRole('button', { name: 'Show hidden (2)' }).click()
  await expect(figures(page)).toHaveCount(8)
  await expect(page.getByText('Hidden: not part of the story')).toHaveCount(2)
  await bar(page).getByRole('button', { name: 'Hide hidden (2)' }).click()
  await expect(figures(page)).toHaveCount(6)

  // Hide the first page of Chapter 2 from the sheet, then restore the chapter's pages.
  await select.selectOption({ label: 'Chapter 2 (2 pages)' })
  await expect(select).toHaveValue('src:c2')
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 2 (3/6)')
  await bar(page).getByRole('button', { name: 'Chapters and pages' }).click()
  await page.getByRole('button', { name: 'Hide first 1' }).click()
  await expect.poll(() => s.visibility).toEqual([{ hidden: true, chapter_id: 'src:c2', edge: 'first', count: 1 }])
  await expect(bar(page).getByRole('button', { name: 'Show hidden (3)' })).toBeVisible()
  await expect(page.getByTestId('comic-chapter-pos')).toHaveText('Page 1 of 1 (3/5)')
  await bar(page).getByRole('button', { name: 'Chapters and pages' }).click()
  await page.getByRole('button', { name: /Restore all in this chapter \(1\)/ }).click()
  await expect(bar(page).getByRole('button', { name: 'Show hidden (2)' })).toBeVisible()
  expect(s.visibility[1]).toEqual({ hidden: false, chapter_id: 'src:c2', edge: 'all' })
  expect(s.unmocked).toEqual([])
})

test('translate scope: this chapter or all chapters, with counts, hidden pages not counted', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', chapters: CHAPTERS, hidden: [7], noRegions: [0, 1, 3] })
  await page.goto('/#/comic/7?page=1')
  await expect(figures(page)).toHaveCount(7)
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  const scope = panel.getByLabel('Translate', { exact: true })
  await expect(scope.locator('option')).toHaveText(['This chapter: Chapter 1 (3 pages)', 'All chapters (7 pages)'])
  await expect(panel.getByRole('button', { name: 'Translate all chapters' })).toBeVisible()
  await expect(panel.getByText(/3 of 7 pages have no text yet/)).toBeVisible()

  await scope.selectOption({ index: 0 })
  await expect(panel.getByText(/2 of 3 pages have no text yet/)).toBeVisible()
  await panel.getByRole('button', { name: 'Translate this chapter' }).click()
  await expect.poll(() => s.runs).toEqual([{ mode: 'missing', chapter_id: 'src:c1', engine: 'ollama', detect_backend: 'auto' }])
  expect(s.unmocked).toEqual([])
})

test('a title with no chapter data reads as one list and still offers Hide', async ({ page }) => {
  const s = await mockScanlate(page, { mediaType: 'manhwa', pageCount: 4 })
  await page.goto('/#/comic/7?page=1')
  await expect(figures(page)).toHaveCount(4)
  await expect(bar(page).getByLabel('Chapter', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('comic-divider')).toHaveCount(0)
  await bar(page).getByRole('button', { name: 'Chapters and pages' }).click()
  await expect(page.getByRole('button', { name: 'Hide page 1' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})
