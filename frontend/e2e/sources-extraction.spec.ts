import { expect as baseExpect, test } from '@playwright/test'

import { COMIC_PAGE_PREVIEW, comicReview, followReview, mockExtraction, novelReview } from './sourcesExtractionMocks'
import { NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'

// Pasted-URL extraction (parity SO09 AI fallback, SO06 comic import, SO10
// Review extraction), desktop. Every job and write is mocked.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

async function paste(page: import('@playwright/test').Page, url: string) {
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill(url)
  await page.getByRole('textbox', { name: 'Paste a link' }).press('Enter')
  return page.getByRole('article', { name: 'Link preview' })
}

test('novel: AI fallback engine, check before importing, correct, save profile, import', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: NOVEL_PREVIEW, urlImportBody: { kind: 'url_import', needs_review: true, char_count: 5120, review_open: true },
  })
  const x = await mockExtraction(page, s, m, { review: novelReview() })
  const card = await paste(page, 'https://novels.example/book/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })

  // The engine list loads only once the AI toggle is on.
  expect(s.calls.some((c) => c.path.endsWith('/url/ai-engines'))).toBe(false)
  await card.getByRole('switch', { name: 'Let AI help if the page is unclear' }).click()
  const engine = card.getByRole('combobox', { name: 'AI engine' })
  await expect(engine).toHaveValue('claude')
  await expect(engine.locator('option')).toHaveText(['Claude (default)', 'Gemini', 'Ollama (on this PC)'])
  await engine.selectOption('ollama')
  await card.getByRole('switch', { name: 'Check before importing' }).click()
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import')[0]?.body).toEqual({
    url: 'https://novels.example/book/5', drama_id: 11, use_ai: true, engine: 'ollama', review: true,
  })
  await expect(card.getByTestId('url-import-result')).toContainText('Nothing was saved yet.')

  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByRole('heading', { name: 'Review extraction' })).toBeVisible()
  await expect(review.getByText('Medium confidence', { exact: true })).toBeVisible()
  await expect(review.getByText('You asked to check the result before it is saved.')).toBeVisible()
  await expect(review.getByLabel('Extracted text (preview)')).toContainText('Xie Lian walked down the mountain.')
  await review.getByText('Confidence per field').click()
  await expect(review.getByText('a comment block may be included', { exact: false })).toBeVisible()
  await expect(review.getByRole('button', { name: 'Save corrections as the site’s profile' })).toBeDisabled()

  // Leave the comments out, number from the link, re-run.
  await review.getByRole('checkbox', { name: 'Comments: great chapter!' }).check()
  await review.getByRole('radio', { name: 'The link' }).check()
  await expect(review.getByRole('combobox', { name: 'Next-chapter link' })).toHaveValue('L0')
  x.rerunBody = novelReview({
    revision: 'r2', can_save_profile: true,
    novel: { ...novelReview().novel, text_preview: 'Xie Lian walked down the mountain.', exclude_selectors: ['.comments'], number_from: 'url' },
  })
  await review.getByRole('button', { name: 'Re-run with these corrections' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/rerun-novel')[0]?.body).toEqual({
    revision: 'r1', content_selector: '#content', exclude_selectors: ['.comments'], title_block: 'b0', next_link: 'L0',
    previous_link: null, number_from: 'url',
  })
  await expect(review.getByLabel('Extracted text (preview)')).not.toContainText('Comments')

  await review.getByRole('button', { name: 'Save corrections as the site’s profile' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/save-profile')[0]?.body).toEqual({ revision: 'r2' })
  await expect(review.getByText(/Saved as profile v2 for novels\.example.*v1 is kept/)).toBeVisible()

  await review.getByRole('button', { name: 'Approve the suggested profile' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/approve-profile').length).toBe(1)
  await expect(review.getByRole('button', { name: 'Approve the suggested profile' })).toHaveCount(0)

  await review.getByRole('button', { name: 'Import this text' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/import')[0]?.body).toEqual({ revision: 'r2' })
  await expect(review.getByTestId('review-import-result')).toContainText('Added 5,120 characters to the title’s novel text.')
  expect(s.unmocked).toEqual([])
})

test('novel: follow next chapters, untick a page, import the rest in order', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: NOVEL_PREVIEW,
    urlImportBody: { kind: 'url_import', needs_review: true, char_count: 14920, review_open: true, pages_found: 3, follow_stop: 'no_next' },
  })
  await mockExtraction(page, s, m, { review: followReview() })
  const card = await paste(page, 'https://novels.example/book/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })

  // Off by default: the page count only shows once following is on.
  await expect(card.getByRole('spinbutton', { name: 'Pages in all' })).toHaveCount(0)
  await card.getByRole('switch', { name: 'Follow next chapters' }).click()
  await expect(card.getByRole('spinbutton', { name: 'Pages in all' })).toHaveValue('10')
  await card.getByRole('spinbutton', { name: 'Pages in all' }).fill('3')
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import')[0]?.body).toEqual({
    url: 'https://novels.example/book/5', drama_id: 11, follow_pages: 3,
  })
  await expect(card.getByTestId('url-import-result')).toContainText('Read 3 pages. Nothing was saved yet.')

  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByText('Baihe followed the next-chapter links.', { exact: false })).toBeVisible()
  const pages = review.getByRole('group', { name: 'Pages read' })
  await expect(pages.getByRole('checkbox')).toHaveCount(3)
  await expect(pages.getByRole('checkbox', { name: /Chapter 6 · 4,800 characters · novels\.example/ })).toBeChecked()
  await expect(pages.getByTestId('follow-stop')).toContainText('The last page has no next-chapter link.')
  await expect(review.getByRole('button', { name: 'Import 3 pages' })).toBeEnabled()

  await pages.getByRole('checkbox', { name: /Chapter 6/ }).uncheck()
  await review.getByRole('button', { name: 'Import 2 pages' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/import')[0]?.body).toEqual({ revision: 'r1', pages: [0, 2] })
  await expect(review.getByTestId('review-import-result')).toContainText('Added 2 pages (10,120 characters) to the title’s novel text.')
  expect(s.unmocked).toEqual([])
})

test('novel: a review that changed elsewhere is reloaded', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: NOVEL_PREVIEW, urlImportBody: { kind: 'url_import', needs_review: true, char_count: 5120, review_open: true },
  })
  const x = await mockExtraction(page, s, m, { review: novelReview({ why: 'low_confidence' }), stale: true })
  const card = await paste(page, 'https://novels.example/book/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })
  await card.getByRole('button', { name: 'Import text' }).click()
  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByText('Baihe isn’t sure it found the right parts of the page', { exact: false })).toBeVisible()
  x.review = novelReview({ revision: 'r9', why: 'low_confidence' })
  await review.getByRole('button', { name: 'Re-run with these corrections' }).click()
  await expect(review.getByText('This review changed since it was loaded.', { exact: false })).toBeVisible()
  await expect.poll(() => s.calls.filter((c) => c.path === '/api/sources/dramas/11/extraction').length).toBe(2)
  await review.getByRole('button', { name: 'Import this text' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/11/extraction/import')[0]?.body).toEqual({ revision: 'r9' })
  expect(s.unmocked).toEqual([])
})

test('comic: import pages with the left-out list, then review roles and order', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: COMIC_PAGE_PREVIEW,
    urlImportBody: {
      kind: 'comic_import', needs_review: false, pages_added: 12, review_open: false, skipped_count: 2,
      skipped: [{ display_url: 'https://comics.example/logo.png', reason: 'too small to be a page (48x48)' },
        { display_url: 'https://ads.example/b.jpg', reason: 'served from a third-party domain (ads.example)' }],
    },
  })
  const x = await mockExtraction(page, s, m, { review: comicReview() })
  const card = await paste(page, 'https://comics.example/read/5')
  await expect(card.getByText('Comic', { exact: true })).toBeVisible()
  const into = card.getByRole('combobox', { name: 'Import into' })
  await expect(into.locator('option')).toHaveText(['Choose a title…', 'Alpha Comic', 'New title…'])
  await into.selectOption({ label: 'Alpha Comic' })
  await card.getByRole('button', { name: 'Import pages' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import-comic')[0]?.body).toEqual({ url: 'https://comics.example/read/5', drama_id: 12 })
  const result = card.getByTestId('comic-import-result')
  await expect(result).toContainText('Added 12 pages to the title.')
  await expect(result.getByRole('link', { name: 'Open pages' })).toHaveAttribute('href', '#/comic/12')
  await result.getByText('2 images left out').click()
  await expect(result.getByText('served from a third-party domain (ads.example)', { exact: false })).toBeVisible()

  // Again, this time the result needs review.
  m.urlImportBody = { kind: 'comic_import', needs_review: true, pages_added: 0, review_open: true, skipped: [], skipped_count: 1 }
  await card.getByRole('button', { name: 'Import pages' }).click()
  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByText('Low confidence', { exact: true })).toBeVisible()
  await expect(review.locator('img')).toHaveCount(4)
  await expect(review.getByRole('combobox', { name: 'Role for image 4' })).toHaveValue('content')
  await expect(review.getByRole('spinbutton', { name: 'Page number for image 1' })).toBeDisabled()
  await expect(review.getByRole('button', { name: 'Apply these corrections' })).toBeDisabled()

  await review.getByRole('combobox', { name: 'Role for image 4' }).selectOption('ad')
  await expect(review.getByRole('spinbutton', { name: 'Page number for image 4' })).toHaveValue('0')
  await review.getByRole('spinbutton', { name: 'Page number for image 2' }).fill('2')
  await review.getByRole('spinbutton', { name: 'Page number for image 3' }).fill('1')
  x.rerunBody = comicReview({
    revision: 'r2', can_save_profile: true,
    comic: { ...comicReview().comic, page_count: 2 },
  })
  await review.getByRole('button', { name: 'Apply these corrections' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/12/extraction/rerun-comic')[0]?.body).toEqual({
    revision: 'r1', images: [{ id: 1, role: 'content', page: 2 }, { id: 2, role: 'content', page: 1 }, { id: 3, role: 'ad', page: 0 }],
  })
  await review.getByRole('button', { name: 'Import 2 pages' }).click()
  await expect.poll(() => posted(s, '/api/sources/dramas/12/extraction/import')[0]?.body).toEqual({ revision: 'r2' })
  await expect(review.getByTestId('review-import-result')).toContainText('Added 2 pages to the title.')
  expect(s.unmocked).toEqual([])
})
