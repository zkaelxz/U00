import { expect, test } from './fixtures'
import { mockSources, posted } from './sourcesMocks'

// Sources: switching between "Search by title" and "Paste a link" keeps what
// was typed in each, and the search results stay on screen. Everything is
// mocked (sourcesMocks.ts); nothing reaches a real site.

test('typed text and search results survive switching between Search by title and Paste a link', async ({ page }) => {
  const s = await mockSources(page)
  await page.goto('/#/sources')
  const title = page.getByRole('searchbox', { name: 'Title' })
  const link = page.getByRole('textbox', { name: 'Paste a link' })

  await title.fill('Heaven')
  await title.press('Enter')
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByTestId('search-results')).toBeVisible()
  await expect(page.getByText('3 results · 2 sources had problems')).toBeVisible()

  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await expect(title).toBeHidden()
  await link.fill('https://example.test/series/1')
  // The results belong to the search, so they are tucked away (not dropped) while pasting.
  await expect(page.getByTestId('search-results')).toBeHidden()

  await page.getByRole('radio', { name: 'Search by title' }).check()
  await expect(title).toHaveValue('Heaven')
  await expect(page.getByTestId('search-results')).toBeVisible()
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await expect(link).toHaveValue('https://example.test/series/1')

  // Only the one search was sent; switching never fetches anything.
  expect(posted(s, '/api/sources/search')).toHaveLength(1)
  expect(s.unmocked).toEqual([])
})
