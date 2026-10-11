import { expect, test, type Locator, type Page } from '@playwright/test'
import { suggestFrom } from './suggestTerms'
import { openFoldFor } from './reviewFolds'
import { openTranscribeOptions } from './sourceHelpers'

// Phone project (390x844, touch): the auto-tune results, glossary-from-novel
// proposals and PC-only delete buttons fit the width, use cards instead of
// tables, and keep 44px touch targets. Jobs, meta and deletes are mocked.

const SHOTS = process.env.SHOT_DIR

async function shot(page: Page, name: string, fullPage = true) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage })
}

async function darkShot(page: Page, name: string, fullPage = true) {
  await page.emulateMedia({ colorScheme: 'dark' })
  await shot(page, name, fullPage)
  await page.emulateMedia({ colorScheme: 'light' })
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTall(loc: Locator) {
  await expect(loc.first()).toBeVisible()
  const n = await loc.count()
  for (let i = 0; i < n; i++) {
    const box = await loc.nth(i).boundingBox()
    expect(box?.height ?? 0, await loc.nth(i).innerText()).toBeGreaterThanOrEqual(44)
  }
}

async function openSection(page: Page, title: string) {
  await openFoldFor(page, title)
  if (['More options', 'Auto-tune min silence'].includes(title)) await openTranscribeOptions(page)
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

test('auto-tune results are cards with 44px Use buttons', async ({ page }) => {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/transcribe/dramas/1/autotune', (route) =>
    route.fulfill({
      json: {
        job_id: 'autotune_1', status: 'done', progress: 1, message: '', best_candidate_ms: 800,
        results: [
          { candidate_ms: 300, long_lines: 9, total_lines: 120 },
          { candidate_ms: 800, long_lines: 2, total_lines: 96 },
          { candidate_ms: 1500, long_lines: 5, total_lines: 70 },
        ],
      },
    }),
  )
  await page.goto('/#/drama/1/source')
  await openSection(page, 'More options')
  await openSection(page, 'Auto-tune min silence')
  const cards = page.locator('ul.autotune-cards > li')
  await expect(cards).toHaveCount(3)
  await expect(cards.nth(1)).toContainText('Fewest long lines')
  await expectTall(page.locator('ul.autotune-cards button'))
  await expectTall(page.getByRole('button', { name: 'Run auto-tune again' }))
  await expectNoHorizontalOverflow(page)
  await cards.nth(1).scrollIntoViewIfNeeded()
  await shot(page, 'autotune-phone')
  await darkShot(page, 'autotune-phone-dark')
})

test('glossary proposals are cards with 44px checkboxes and one primary', async ({ page }) => {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: true, char_count: 900, chapters: 3, ocr_running: false } }),
  )
  const prop = (term: string, en: string, already = false) => ({
    term, suggested_translation: en, category: 'person', policy: 'keep', reason: 'Recurring name in chapters 1-3', already_in_glossary: already, occurrences: 3, alternatives: [], confidence: 'high',
  })
  await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
    route.fulfill({
      json: {
        job_id: 'novelglossary_1', status: 'done', progress: 1, message: '',
        proposals: [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan'), prop('云深不知处', 'Cloud Recesses'), prop('江澄', 'Jiang Cheng', true)],
      },
    }),
  )
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Glossary')
  await suggestFrom(page, 'Novel')
  const box = page.getByTestId('novel-glossary')
  await expect(box.locator('ul.novel-glossary-cards > li')).toHaveCount(4)
  await expectTall(box.locator('ul.novel-glossary-cards label'))
  await expect(box.locator('button.primary')).toHaveCount(1)
  await expectTall(box.locator('button.primary'))
  // The Fresh suggestions row sits by the start button and fits the width.
  await expect(box.getByRole('switch', { name: 'Fresh suggestions' })).toBeVisible()
  await expectTall(box.locator('.setting-list > .field-item', { hasText: 'Fresh suggestions' }))
  await expectNoHorizontalOverflow(page)
  await box.scrollIntoViewIfNeeded()
  await shot(page, 'glossary-from-novel-phone')
  await darkShot(page, 'glossary-from-novel-phone-dark')
})

test('with 30 proposals the apply row stays in reach at the bottom', async ({ page }) => {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: true, char_count: 900, chapters: 3, ocr_running: false } }),
  )
  const proposals = Array.from({ length: 30 }, (_, i) => ({
    term: `术语${i + 1}`, suggested_translation: `Term ${i + 1}`, category: 'term', policy: 'translate',
    reason: 'Appears in several chapters', already_in_glossary: i % 7 === 0, occurrences: 3, alternatives: [], confidence: 'high',
  }))
  await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
    route.fulfill({ json: { job_id: 'novelglossary_1', status: 'done', progress: 1, message: '', proposals } }),
  )
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Glossary')
  await suggestFrom(page, 'Novel')
  const box = page.getByTestId('novel-glossary')
  await expect(box.locator('ul.novel-glossary-cards > li')).toHaveCount(30)
  await box.locator('ul.novel-glossary-cards > li').nth(10).scrollIntoViewIfNeeded()
  const primary = box.locator('button.primary')
  await expect(primary).toHaveText('Add 25 terms to series glossary')
  const b = await primary.boundingBox()
  expect(b, 'primary is rendered').not.toBeNull()
  expect(b!.y + b!.height, 'primary is inside the viewport').toBeLessThanOrEqual(844)
  expect(b!.y, 'primary is inside the viewport').toBeGreaterThanOrEqual(0)
  await expectNoHorizontalOverflow(page)
  await shot(page, 'glossary-30-proposals-sticky-phone', false)
  await darkShot(page, 'glossary-30-proposals-sticky-phone-dark', false)
})

test('PC-only delete buttons are 44px and on their own line', async ({ page }) => {
  await page.route('**/api/review/dramas/1/versions', (route) =>
    route.fulfill({
      json: [{ id: 9, drama_id: 1, label: 'Claude pass 1', engine: 'claude', model: 'm', is_active: true, created_at: '2026-09-01' }],
    }),
  )
  await page.goto('/#/drama/1/review')
  await openSection(page, 'Records')
  const list = page.getByTestId('versions-list')
  await expectTall(list.getByRole('button', { name: 'Delete Claude pass 1', exact: true }))
  await list.getByRole('button', { name: 'Delete Claude pass 1', exact: true }).click()
  await expectTall(list.getByRole('button', { name: 'Confirm delete Claude pass 1' }))
  await expectNoHorizontalOverflow(page)
  await list.scrollIntoViewIfNeeded()
  await shot(page, 'records-version-delete-armed-phone')

  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.goto('/#/drama/1/source')
  await expectTall(page.getByRole('button', { name: 'Remove audio/video', exact: true }))
  await expectNoHorizontalOverflow(page)
  await shot(page, 'source-remove-media-phone')
})

test('proposal cards show confidence, offer Ignore and Select all High at 44px', async ({ page }) => {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: true, char_count: 900, chapters: 3, ocr_running: false } }),
  )
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/dramas/1/dismissals', (route) =>
    route.fulfill({ json: route.request().method() === 'GET' ? { dismissals: [{ term: '路人', created_at: null }] } : { changed: 1 } }),
  )
  await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
    route.fulfill({
      json: {
        job_id: 'novelglossary_1', status: 'done', progress: 1, message: '', run_id: 'run-1',
        proposals: [
          { term: '魏婴', suggested_translation: 'Wei Ying', category: null, policy: null, reason: '', already_in_glossary: false, occurrences: 12, alternatives: [], confidence: 'high' },
          { term: '云深', suggested_translation: 'Cloud', category: null, policy: null, reason: '', already_in_glossary: false, occurrences: 2, alternatives: ['Deep Clouds'], confidence: 'low' },
        ],
      },
    }),
  )
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Glossary')
  await suggestFrom(page, 'Novel')
  const cards = page.getByTestId('novel-glossary-proposals').locator('li')
  await expect(cards).toHaveCount(2)
  await expect(cards.nth(0)).toContainText('Seen 12×')
  await expect(cards.nth(1)).toContainText('Seen 2× · also: Deep Clouds')
  await expectTall(page.getByRole('button', { name: /^Ignore (魏婴|云深)$/ }))
  await expectTall(page.getByRole('button', { name: 'Select all High (1)' }))
  await expectTall(page.getByRole('button', { name: 'Ignored (1)' }))
  await expectNoHorizontalOverflow(page)
  await shot(page, 'glossary-proposals-confidence-phone')
  await darkShot(page, 'glossary-proposals-confidence-phone-dark')
})
