import { expect, test } from './fixtures'

// The series folder list (#/series): one folder per series, a title's cover when the
// series has one, a folder icon when it has none, and a click opens the series.
// /api/series and the cover are mocked so the seeded library is untouched.

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

const SERIES = [
  { id: 9, name: 'Birch clips', is_private: false, owned_by_me: true, title_count: 2, glossary_term_count: 0, cover_title_id: 41 },
  { id: 12, name: 'Cedar', is_private: false, owned_by_me: true, title_count: 1, glossary_term_count: 0, cover_title_id: null },
]

test('series folders show a cover or a folder icon and open the series', async ({ page }) => {
  await page.route('**/api/series', (route) => route.fulfill({ json: { items: SERIES } }))
  await page.route('**/api/dramas/41/cover*', (route) => route.fulfill({ body: PNG, contentType: 'image/png' }))

  await page.goto('/#/series')
  const folders = page.getByTestId('series-folders')
  await expect(folders.getByRole('link')).toHaveCount(2)
  await expect(folders.getByRole('link', { name: /Birch clips/ })).toContainText('2 titles')
  await expect(folders.getByRole('link', { name: /Cedar/ })).toContainText('1 title')
  await expect(folders.locator('img.series-folder-cover')).toHaveCount(1)
  await expect(folders.locator('.series-folder-icon')).toHaveCount(1)

  await folders.getByRole('link', { name: /Cedar/ }).click()
  await expect(page).toHaveURL(/#\/series\/12/)
})

test('once a series has a streamer, streamers come first, then their series', async ({ page }) => {
  const items = [
    { ...SERIES[0], streamer: 'Birch 德塔' },
    { id: 13, name: 'Birch HS', is_private: false, owned_by_me: true, title_count: 3, glossary_term_count: 0, cover_title_id: null, streamer: 'Birch 德塔' },
    { ...SERIES[1], streamer: null },
  ]
  await page.route('**/api/series', (route) => route.fulfill({ json: { items } }))
  await page.route('**/api/dramas/41/cover*', (route) => route.fulfill({ body: PNG, contentType: 'image/png' }))

  await page.goto('/#/series')
  const streamers = page.getByTestId('streamer-folders')
  await expect(streamers.getByRole('link')).toHaveCount(2)
  await expect(streamers.getByRole('link').first()).toContainText('Birch 德塔')
  await expect(streamers.getByRole('link').first()).toContainText('2 series · 5 titles')
  await expect(streamers.getByRole('link').last()).toContainText('No streamer')

  await streamers.getByRole('link', { name: /Birch/ }).click()
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Streamers')
  const folders = page.getByTestId('series-folders')
  await expect(folders.getByRole('link')).toHaveCount(2)
  await page.getByRole('link', { name: 'Streamers' }).click()
  await streamers.getByRole('link', { name: /No streamer/ }).click()
  await expect(folders.getByRole('link')).toHaveCount(1)
  await folders.getByRole('link', { name: /Cedar/ }).click()
  await expect(page).toHaveURL(/#\/series\/12/)
})
