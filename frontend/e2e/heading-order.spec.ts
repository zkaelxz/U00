import { test } from './fixtures'
import { expectHeadingOrderOnEveryTab } from './headingOrder'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'
import { enableDeveloperMode } from './sourceHelpers'

// Screen readers jump through a page by heading level, so headings step down one level at a time: the page
// title is the h1, a section under it an h2, a part of that section an h3. Checked with every fold and tab open.

const PAGES = [
  '#/', '#/library-tools', '#/jobs', '#/settings', '#/admin', '#/translate', '#/make-subtitles', '#/sources',
  '#/discover', '#/live', '#/diagnostics', '#/assistant', '#/benchmark', '#/manga', '#/read/2', '#/comic/2',
  '#/series/7',
  '#/drama/1/source', '#/drama/1/translate', '#/drama/1/review', '#/drama/1/export',
  '#/drama/2/source', '#/drama/2/translate', '#/drama/2/export',
  '#/drama/3/source', '#/drama/3/translate', '#/drama/3/export',
]

for (const hash of PAGES) {
  test(`headings never skip a level: ${hash}`, async ({ page }) => {
    // The Benchmark Lab page is behind Developer Mode.
    await enableDeveloperMode(page)
    await page.goto(`/${hash}`)
    await page.locator('main h1').first().waitFor()
    await expectHeadingOrderOnEveryTab(page)
  })
}

test.describe('Review with lines and check results', () => {
  test.beforeEach(() => seedReviewResults())
  test.afterAll(() => clearReviewResults())

  test('headings never skip a level: #/drama/3/review', async ({ page }) => {
    await page.goto('/#/drama/3/review')
    await page.locator('.review-line:not(.review-skeleton)').first().click()
    await expectHeadingOrderOnEveryTab(page)
  })
})
