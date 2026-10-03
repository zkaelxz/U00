import { expect, test, type Page } from '@playwright/test'

import { mockManga, SERIES, W } from './savedMangaMocks'

// Saved manga (#/manga): chapters saved as CBZ files, listed and read in the
// app, and the PC-only save folder card. Every /api call is mocked
// (savedMangaMocks.ts); a catch-all aborts anything else and each test checks
// nothing fell through.

const label = (page: Page) => page.getByTestId('comic-page-label')

test('lists saved series, reads a chapter, continues where it left off', async ({ page }) => {
  const s = await mockManga(page)
  await page.goto('/#/manga')
  // Saved manga sits under Library in the nav.
  await expect(page.getByRole('link', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('heading', { name: 'Saved manga' })).toBeVisible()
  const list = page.getByRole('list', { name: 'Saved series' })
  await expect(list).toContainText('MangaK · 2 chapters · last saved 2 min ago')
  // Nothing read yet: no Continue.
  await expect(list.getByRole('link', { name: 'Continue' })).toHaveCount(0)

  await list.getByRole('link', { name: 'Chapters' }).click()
  await expect(page).toHaveURL('/#/manga/MangaK/Test%20Camp')
  await expect(page.getByRole('heading', { name: SERIES })).toBeVisible()
  await page.getByRole('link', { name: 'Start reading' }).click()

  await expect(page).toHaveURL(/#\/manga\/MangaK\/Test%20Camp\/0001%20Chapter%201\?page=1$/)
  await expect(label(page)).toHaveText('Page 1 of 4')
  await expect(page.getByTestId('comic-page')).toHaveCount(4)
  await expect(page.getByTestId('comic-page').first().locator('img')).toHaveAttribute('width', String(W))
  await page.keyboard.press('ArrowRight')
  await expect(label(page)).toHaveText('Page 2 of 4')
  await expect(page).toHaveURL(/page=2$/)
  await expect.poll(() => s.images.includes('0001 Chapter 1:3')).toBe(true)

  // Back to the list: Continue goes to chapter 1, page 2.
  await page.goto('/#/manga')
  await page.getByRole('list', { name: 'Saved series' }).getByRole('link', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/0001%20Chapter%201\?page=2$/)
  await expect(label(page)).toHaveText('Page 2 of 4')

  // One page at a time from "Aa"; at the last page the next chapter is offered.
  await page.getByRole('button', { name: 'View settings' }).click()
  await page.getByLabel('Reading mode', { exact: true }).selectOption('paged')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('comic-page')).toHaveCount(1)
  await expect(page.getByRole('link', { name: 'Next chapter' })).toHaveCount(0)
  await page.keyboard.press('End')
  await expect(label(page)).toHaveText('Page 4 of 4')
  await page.getByRole('link', { name: 'Next chapter' }).click()
  await expect(page).toHaveURL(/0002%20Chapter%202\?page=1$/)
  await expect(label(page)).toHaveText('Page 1 of 3')
  // The view setting is kept for the series.
  await expect(page.getByTestId('comic-page')).toHaveCount(1)

  await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: SERIES }).click()
  await expect(page.getByRole('list', { name: 'Chapters' })).toContainText('Chapter 2 · reading, page 1')
  expect(s.unmocked).toEqual([])
})

test('save folder: pick one, go back to the default, open it', async ({ page }) => {
  const s = await mockManga(page, { empty: true })
  await page.goto('/#/manga')
  await expect(page.getByTestId('manga-empty')).toContainText('No saved chapters yet')
  const card = page.getByRole('region', { name: 'Save folder' })
  await expect(card.getByTestId('save-folder')).toHaveText('C:\\Baihe\\data\\saved_comics')
  await expect(card).toContainText('Default')

  const input = card.getByLabel('Save to another folder', { exact: true })
  await input.fill('relative')
  await card.getByRole('button', { name: 'Use this folder' }).click()
  await expect(card.getByRole('alert')).toContainText('starting with its drive')

  await input.fill('D:\\Manga')
  await card.getByRole('button', { name: 'Use this folder' }).click()
  await expect(card.getByTestId('save-folder')).toHaveText('D:\\Manga')
  await expect(card).toContainText('Picked on this PC')
  await expect(input).toHaveValue('')

  await card.getByRole('button', { name: 'Use the default' }).click()
  await expect(card.getByTestId('save-folder')).toHaveText('C:\\Baihe\\data\\saved_comics')
  expect(s.folderPosts).toEqual([{ folder: 'relative' }, { folder: 'D:\\Manga' }, { folder: '' }])

  await card.getByRole('button', { name: 'Open folder' }).click()
  await expect.poll(() => s.opened).toBe(1)
  expect(s.unmocked).toEqual([])
})

test('from another device the save folder card is not shown', async ({ page }) => {
  const s = await mockManga(page, { local: false })
  await page.goto('/#/manga')
  await expect(page.getByRole('list', { name: 'Saved series' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Save folder' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open folder' })).toHaveCount(0)
  // Asked at most once before /api/meta answered (refused, so the card stays hidden); never opened or changed.
  expect(s.calls.filter((c) => c !== 'GET /api/saved-comics/folder')).toEqual([])
  expect(s.unmocked).toEqual([])
})
