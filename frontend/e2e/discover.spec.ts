import { expect, test, type Page } from '@playwright/test'

import { mockDiscover, posts } from './discoverMocks'

// Desktop: the Discover page (#/discover). Every /api/discover call is mocked (discoverMocks.ts).

const openSection = (page: Page, title: string) =>
  page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).click()

test('nav entry, empty catalogue loads starter titles, search and filters', async ({ page }) => {
  const s = await mockDiscover(page, { titles: [] })
  await page.goto('/#/discover')
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByTestId('catalog-count')).toHaveText('Your catalogue is empty.')
  await page.getByRole('button', { name: 'Load starter titles' }).click()
  await expect(page.getByText('Added 2 starter titles.')).toBeVisible()
  await expect(page.getByTestId('catalog-count')).toHaveText('2 of 2 saved titles')

  await page.getByRole('searchbox', { name: 'Search saved titles' }).fill('moon')
  await expect(page.getByTestId('catalog-count')).toHaveText('1 of 2 saved titles matching “moon”')
  await page.getByRole('searchbox', { name: 'Search saved titles' }).fill('')
  await page.getByLabel('Language', { exact: true }).first().selectOption('zh')
  await expect(page.getByTestId('catalog-list').getByRole('listitem')).toHaveCount(1)
  await expect.poll(() => s.calls.some((c) => c.path === '/api/discover/titles?language=zh')).toBe(true)

  const card = page.getByTestId('catalog-list').getByRole('listitem').first()
  await card.getByText('Details').click()
  await expect(card.getByText('A general and a princess.')).toBeVisible()
  await expect(card.getByRole('link', { name: 'example.cn' })).toHaveAttribute('href', 'https://example.cn/t/1')
  expect(s.unmocked).toEqual([])
})

test('add to Library, already-added 409, PC-only remove', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  const list = page.getByTestId('catalog-list')
  await list.getByRole('button', { name: 'Add 女将军和长公主 to Library' }).click()
  await expect(page.getByText('Added “The General and the Princess” to your Library.')).toBeVisible()
  await expect(list.getByRole('link', { name: 'In your Library — open' })).toHaveAttribute('href', '#/drama/42')

  s.importConflict = true
  await list.getByRole('button', { name: 'Add 月の庭 to Library' }).click()
  await expect(page.getByText('That title is already in your Library.')).toBeVisible()
  await expect(list.getByRole('link', { name: 'In your Library — open' }).nth(1)).toHaveAttribute('href', '#/drama/3')

  await list.getByRole('button', { name: 'Remove 月の庭' }).click()
  await list.getByRole('button', { name: /Confirm remove 月の庭/ }).click()
  await expect(list.getByRole('listitem')).toHaveCount(1)
  const del = posts(s, '/titles/2/delete')
  expect(del).toHaveLength(1)
  expect(del[0].body).toEqual({ confirm: true })
  expect(del[0].headers['x-baihe-local']).toBe('1')
  expect(s.unmocked).toEqual([])
})

test('remote viewer: no remove button', async ({ page }) => {
  await mockDiscover(page, { local: false })
  await page.goto('/#/discover')
  await expect(page.getByTestId('catalog-list').getByText('Deleting is PC only.').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /^Remove / })).toHaveCount(0)
})

test('find on platforms translates an English title with the picked engine', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  // Only engines the Discover routes accept are offered.
  const picker = page.getByLabel('AI engine', { exact: true })
  await expect(picker.locator('option')).toHaveText(['Claude', 'Ollama (local) (free)'])
  await picker.selectOption('ollama')
  await page.getByRole('searchbox', { name: 'Title to find' }).fill('The General')
  await page.getByRole('button', { name: 'Find', exact: true }).click()
  await expect(page.getByText('Searching Chinese platforms for: 女将军')).toBeVisible()
  await expect(page.getByTestId('search-links').getByRole('link')).toHaveCount(2)
  expect(posts(s, '/translate-query')[0].body).toEqual({ q: 'The General', engine: 'ollama' })
  expect(s.calls.some((c) => c.path.startsWith('/api/discover/search-links?q=%E5%A5%B3%E5%B0%86%E5%86%9B'))).toBe(true)

  // A second Find with the same text reuses the translation.
  await page.getByRole('button', { name: 'Find', exact: true }).click()
  await expect(page.getByTestId('search-links')).toBeVisible()
  expect(posts(s, '/translate-query')).toHaveLength(1)

  await page.getByLabel('Genre').selectOption('any')
  await page.getByLabel('JJWXC tag').fill('言情')
  await page.getByRole('button', { name: 'Find', exact: true }).click()
  await expect.poll(() => s.calls.some((c) => c.path.includes('genre=any&tag=%E8%A8%80%E6%83%85'))).toBe(true)

  await page.getByText('Known official platforms').click()
  const platforms = page.getByTestId('platforms')
  await expect(platforms.getByRole('link', { name: 'JJWXC (晋江文学城)' })).toHaveAttribute('target', '_blank')
  // A non-http address is shown as text, never linked.
  await expect(platforms.getByText('Bad link site')).toBeVisible()
  await expect(platforms.getByRole('link', { name: 'Bad link site' })).toHaveCount(0)
  await page.screenshot({ path: 'test-results/discover-desktop.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('baihehub search falls back to a browser link; navigation helper shows steps', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  await openSection(page, 'Search baihehub')
  await page.getByRole('searchbox', { name: 'Title to search' }).fill('长公主')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  const bh = page.getByTestId('baihehub-result')
  await expect(bh.getByRole('link', { name: 'Run this search in your browser' })).toHaveAttribute('href', 'https://baihehub.com/search?q=x')
  expect(posts(s, '/translate-query')).toHaveLength(0) // already Chinese

  await openSection(page, 'Site navigation helper')
  const go = page.getByRole('button', { name: 'Get navigation steps' })
  await expect(go).toBeDisabled()
  await page.getByLabel('Start from a known site').selectOption({ label: 'JJWXC (晋江文学城)' })
  await expect(page.getByLabel('Page URL', { exact: true })).toHaveValue('https://www.jjwxc.net')
  await page.getByLabel('What are you trying to do?').fill('find audio dramas')
  await go.click()
  const result = page.getByTestId('nav-result')
  await expect(result.getByText(/Click \*\*广播剧\*\*/)).toBeVisible()
  await result.getByText('Translated page labels (2)').click()
  await expect(result.getByText('广播剧 → Audio dramas')).toBeVisible()
  expect(posts(s, '/navigation-help')[0].body).toEqual({
    url: 'https://www.jjwxc.net', goal: 'find audio dramas', target_language: 'English', engine: 'claude',
  })
  expect(s.unmocked).toEqual([])
})

test('add a title from a URL suggestion, then by hand', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  await page.getByLabel('Fill from a page (optional)').fill('https://example.cn/snow')
  await page.getByRole('button', { name: 'Read page' }).click()
  await expect(page.getByLabel('Title (original language)')).toHaveValue('雪夜')
  await expect(page.getByLabel('Title (English)')).toHaveValue('Snow Night')
  await page.getByLabel('Media type').selectOption('audio_drama')
  await page.getByRole('button', { name: 'Add to catalogue' }).click()
  await expect(page.getByText('Added “雪夜” to your catalogue.')).toBeVisible()
  expect(posts(s, '/api/discover/titles')[0].body).toEqual({
    title_original: '雪夜', title_en: 'Snow Night', author: 'Lin', tags: '', summary_en: 'Two girls, one winter.',
    source_name: 'url', source_url: 'https://example.cn/snow', language: 'zh', media_type: 'audio_drama',
  })
  await expect(page.getByTestId('catalog-count')).toHaveText('3 of 3 saved titles')

  // By hand: the title is required.
  await page.getByRole('button', { name: 'Add to catalogue' }).click()
  await expect(page.getByText('Enter the title in its original language.')).toBeVisible()
  expect(posts(s, '/api/discover/titles')).toHaveLength(1)
  expect(s.unmocked).toEqual([])
})

test('bulk import: pattern, extract job, review, add', async ({ page }) => {
  const s = await mockDiscover(page)
  await page.goto('/#/discover')
  await openSection(page, 'Bulk import from listing pages')
  await page.getByText('Fill in page URLs from a pattern').click()
  await page.getByLabel('URL pattern').fill('https://www.jjwxc.net/tag.php?page={page}')
  await page.getByLabel('To page').fill('2')
  await page.getByRole('button', { name: 'Fill in 2 URLs' }).click()
  await expect(page.getByRole('textbox', { name: 'Listing page URLs' })).toHaveValue(
    'https://www.jjwxc.net/tag.php?page=1\nhttps://www.jjwxc.net/tag.php?page=2',
  )
  await page.getByRole('button', { name: 'Extract entries' }).click()
  await expect(page.getByText(/Read 1 of 2 pages/)).toBeVisible()
  expect(posts(s, '/bulk-extract')[0].body).toEqual({
    urls: ['https://www.jjwxc.net/tag.php?page=1', 'https://www.jjwxc.net/tag.php?page=2'],
    source_label: 'jjwxc_baihe_tag', engine: 'claude',
  })
  s.bulk = 'done'
  const review = page.getByTestId('bulk-review')
  await expect(review.getByText('Review 3 entries')).toBeVisible()
  await expect(review.getByText('1 of 2 pages couldn\'t be read:')).toBeVisible()
  await expect(review.getByText(/build their listings with JavaScript/)).toBeVisible()
  await review.getByRole('checkbox', { name: /青梅/ }).uncheck()
  await review.getByRole('button', { name: 'Add 2 to catalogue' }).click()
  await expect(review.getByText('Added 1 title to your catalogue (1 already there).')).toBeVisible()
  const body = posts(s, '/bulk-commit')[0].body as { entries: { entry_id: string }[]; source_label: string }
  expect(body.entries.map((e) => e.entry_id)).toEqual(['r-0', 'r-2'])
  expect(body.source_label).toBe('jjwxc_baihe_tag')
  expect(s.unmocked).toEqual([])
})

test('no configured engine: AI actions say what is missing', async ({ page }) => {
  await mockDiscover(page, { engines: [{ name: 'deepl', label: 'DeepL', free: false, models: null, key_configured: true }] })
  await page.goto('/#/discover')
  await expect(page.getByTestId('no-engine')).toBeVisible()
  await expect(page.getByText('No AI engine is set up, so the title is searched as typed.')).toBeVisible()
  await openSection(page, 'Bulk import from listing pages')
  await expect(page.getByRole('button', { name: 'Extract entries' })).toBeDisabled()
})
