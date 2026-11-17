import { expect, test } from './fixtures'

// The series folder list (#/series): one folder per series, a title's cover when the
// series has one, a folder icon when it has none, and a click opens the series.
// /api/series and the cover are mocked so the seeded library is untouched.

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

const SERIES = [
  { id: 9, name: 'Chion clips', is_private: false, owned_by_me: true, title_count: 2, glossary_term_count: 0, cover_title_id: 41 },
  { id: 12, name: 'Uririn', is_private: false, owned_by_me: true, title_count: 1, glossary_term_count: 0, cover_title_id: null },
]

test('series folders show a cover or a folder icon and open the series', async ({ page }) => {
  await page.route('**/api/series', (route) => route.fulfill({ json: { items: SERIES } }))
  await page.route('**/api/dramas/41/cover*', (route) => route.fulfill({ body: PNG, contentType: 'image/png' }))

  await page.goto('/#/series')
  const folders = page.getByTestId('series-folders')
  await expect(folders.getByRole('link')).toHaveCount(2)
  await expect(folders.getByRole('link', { name: /Chion clips/ })).toContainText('2 titles')
  await expect(folders.getByRole('link', { name: /Uririn/ })).toContainText('1 title')
  await expect(folders.locator('img.series-folder-cover')).toHaveCount(1)
  await expect(folders.locator('.series-folder-icon')).toHaveCount(1)

  await folders.getByRole('link', { name: /Uririn/ }).click()
  await expect(page).toHaveURL(/#\/series\/12/)
})
