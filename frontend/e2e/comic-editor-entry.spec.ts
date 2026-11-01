import { expect, test } from '@playwright/test'

import { mockComic } from './comicMocks'

// The Comic page's Editor button opens the editor at the page being read.
test('Editor opens the editor at the current page', async ({ page }) => {
  await mockComic(page, { mediaType: 'manhua', lastPage: 3 })
  await page.goto('/#/comic/7')
  await expect(page.getByTestId('comic-page-label')).toHaveText('Page 3 of 8')
  const editor = page.getByRole('link', { name: 'Editor' })
  await expect(editor).toHaveAttribute('href', '#/comic/7/edit?page=3')
})
