import { expect as baseExpect, test, type Page } from '@playwright/test'

import { COMIC_PAGE_PREVIEW, comicReview, followReview, mockExtraction, novelReview } from './sourcesExtractionMocks'
import { NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { mockSources } from './sourcesMocks'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the pasted-URL AI option and the Review
// extraction step (parity SO09, SO06, SO10) fit the screen and have 44 px
// targets. Every job and write is mocked.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tallTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, input[type="number"], input[type="url"], .extraction-fieldset label, .sources-skipped summary, .extraction-details summary'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

async function paste(page: Page, url: string) {
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill(url)
  await page.getByRole('button', { name: 'Preview' }).click()
  return page.getByRole('article', { name: 'Link preview' })
}

test('phone: novel review with the AI option fits and has 44 px targets', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: NOVEL_PREVIEW, urlImportBody: { kind: 'url_import', needs_review: true, char_count: 5120, review_open: true },
  })
  await mockExtraction(page, s, m, { review: novelReview() })
  const card = await paste(page, 'https://novels.example/book/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })
  await card.getByRole('switch', { name: 'Let AI help if the page is unclear' }).click()
  await expect(card.getByRole('combobox', { name: 'AI engine' })).toHaveValue('claude')
  await card.getByRole('button', { name: 'Import text' }).click()
  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByRole('button', { name: 'Re-run with these corrections' })).toBeVisible()
  await noSideways(page)
  await tallTargets(page)
  expect(s.unmocked).toEqual([])
})

test('phone: following next chapters and the page list fit and have 44 px targets', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: NOVEL_PREVIEW,
    urlImportBody: { kind: 'url_import', needs_review: true, char_count: 14920, review_open: true, pages_found: 3, follow_stop: 'cap' },
  })
  await mockExtraction(page, s, m, { review: followReview() })
  const card = await paste(page, 'https://novels.example/book/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })
  await card.getByRole('switch', { name: 'Follow next chapters' }).click()
  await expect(card.getByRole('spinbutton', { name: 'Pages in all' })).toBeVisible()
  await noSideways(page)
  await tallTargets(page)
  await card.getByRole('button', { name: 'Import text' }).click()
  const pages = card.getByRole('group', { name: 'Pages read' })
  await expect(pages.getByRole('checkbox')).toHaveCount(3)
  await pages.scrollIntoViewIfNeeded()
  await noSideways(page)
  await tallTargets(page)
  expect(s.unmocked).toEqual([])
})

test('phone: comic review rows stack and fit', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, {
    previewBody: COMIC_PAGE_PREVIEW,
    urlImportBody: { kind: 'comic_import', needs_review: true, pages_added: 0, review_open: true, skipped: [], skipped_count: 1 },
  })
  await mockExtraction(page, s, m, { review: comicReview() })
  const card = await paste(page, 'https://comics.example/read/5')
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Alpha Comic' })
  await card.getByRole('button', { name: 'Import pages' }).click()
  const review = card.getByRole('region', { name: 'Review extraction' })
  await expect(review.getByRole('combobox', { name: 'Role for image 1' })).toBeVisible()
  await review.getByRole('combobox', { name: 'Role for image 4' }).scrollIntoViewIfNeeded()
  await noSideways(page)
  await tallTargets(page)
  const role = (await review.getByRole('combobox', { name: 'Role for image 2' }).boundingBox())!
  expect(role.x + role.width).toBeLessThanOrEqual(390)
  await page.screenshot({ path: 'test-results/sources-extraction-comic-phone.png', fullPage: false })
  expect(s.unmocked).toEqual([])
})
