import { expect, test, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

// Glossary helpers: Glossary → From lines (parity X10), Review glossary
// before translating (X28) and the novel glossary on the Source stage (T02).
// Drama reads hit the real seeded API (drama 1, reported as in series 7);
// the extraction jobs, applies, translate run and job polls are mocked, so
// no engine is ever called.

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function openSection(page: Page, title: string) {
  await page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }).first().click()
}

const notFound = (route: Route) => route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } })

async function base(page: Page, { novel }: { novel: boolean }) {
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: novel, char_count: novel ? 900 : 0, chapters: novel ? 3 : 0, ocr_running: false } }),
  )
}

const prop = (term: string, en: string, already = false) => ({
  term, suggested_translation: en, category: 'person_name', policy: 'keep_pinyin', reason: 'Recurring name', already_in_glossary: already,
})

// A mocked extraction route: 404 until started, running for one read, then done.
function mockRun(page: Page, kind: 'novel' | 'lines', proposals: object[], starts: string[]) {
  let state: 'none' | 'running' | 'done' = 'none'
  const job = `${kind}_glossary_1`
  return page.route(`**/api/glossary/dramas/1/from-${kind}`, (route) => {
    if (route.request().method() === 'POST') {
      starts.push(kind)
      state = 'running'
      return route.fulfill({ json: kind === 'lines' ? { job_id: job, engine: 'claude', line_count: 12 } : { job_id: job, engine: 'claude', paired: false } })
    }
    if (state === 'none') return notFound(route)
    if (state === 'running') {
      state = 'done'
      return route.fulfill({ json: { job_id: job, status: 'running', progress: 0.1, message: '', proposals: null } })
    }
    return route.fulfill({ json: { job_id: job, status: 'done', progress: 1, message: '', proposals } })
  })
}

async function mockTranslateRun(page: Page) {
  const bodies: unknown[] = []
  await page.route('**/api/translate-run/dramas/1/run', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { job_id: 'tr-job', drama_id: 1, engine: 'claude', model: null, target_line_count: 0, fallback_engines: [] } })
  })
  await page.route('**/api/jobs/tr-job', (route) =>
    route.fulfill({
      json: { job_id: 'tr-job', status: 'running', progress: 0.5, message: 'translating', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 },
    }),
  )
  return bodies
}

test.describe('desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('From lines: extracts, edits a translation, and adds only the checked terms by text', async ({ page }) => {
    await base(page, { novel: false })
    const starts: string[] = []
    await mockRun(page, 'lines', [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan'), prop('江澄', 'Jiang Cheng', true)], starts)
    let termReads = 0
    await page.route('**/api/glossary/dramas/1/terms', (route) => {
      termReads += 1
      return route.fulfill({ json: [] })
    })
    let applyBody = ''
    await page.route('**/api/glossary/dramas/1/from-lines/apply', (route) => {
      applyBody = route.request().postData() ?? ''
      return route.fulfill({ json: { added: ['魏婴'], overwritten: [], skipped_existing: [], unknown: [] } })
    })

    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From lines')
    const box = page.getByTestId('lines-glossary')
    await expect(box).toContainText("this drama's source lines")
    await box.getByRole('button', { name: 'Extract terms' }).click()
    const table = box.getByTestId('lines-glossary-proposals')
    await expect(table.locator('tbody tr')).toHaveCount(3)
    expect(starts).toEqual(['lines'])
    await expect(box.getByRole('button', { name: 'Add 2 terms to series glossary' })).toBeVisible()

    await box.getByLabel('Select 蓝湛').uncheck()
    await box.getByLabel('Translation for 魏婴').fill('Wei Wuxian')
    await box.getByLabel('Translation for 魏婴').fill('')
    await expect(box.getByText('Add a translation for 魏婴 first.')).toBeVisible()
    await expect(box.getByRole('button', { name: 'Add 1 term to series glossary' })).toBeDisabled()
    await box.getByLabel('Translation for 魏婴').fill('Wei Wuxian')
    await box.getByLabel('Policy for 魏婴').selectOption('hybrid')
    await shot(page, 'glossary-from-lines-desktop')
    const readsBefore = termReads
    await box.getByRole('button', { name: 'Add 1 term to series glossary' }).click()
    await expect(box).toContainText('Added 1.')
    expect(JSON.parse(applyBody)).toEqual({
      terms: ['魏婴'],
      overrides: { 魏婴: { translation: 'Wei Wuxian', policy: 'hybrid' } },
    })
    // The glossary table re-reads after adding.
    await expect.poll(() => termReads).toBeGreaterThan(readsBefore)
  })

  test('Review glossary first (novel attached): proposals, then add and start the run', async ({ page }) => {
    await base(page, { novel: true })
    const starts: string[] = []
    await mockRun(page, 'novel', [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan')], starts)
    let applyBody = ''
    await page.route('**/api/glossary/dramas/1/from-novel/apply', (route) => {
      applyBody = route.request().postData() ?? ''
      return route.fulfill({ json: { added: ['魏婴'], overwritten: [], skipped_existing: [], unknown: [] } })
    })
    const runs = await mockTranslateRun(page)

    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByLabel('Review glossary before translating').check()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review).toContainText('the attached novel')
    await expect(review.getByTestId('glossary-review-proposals').locator('tbody tr')).toHaveCount(2)
    expect(starts).toEqual(['novel'])
    expect(runs).toHaveLength(0)
    await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeDisabled()
    await review.getByLabel('Select 蓝湛').uncheck()
    await shot(page, 'glossary-review-desktop')
    await review.getByRole('button', { name: 'Add 1 term and start translation' }).click()
    await expect(page.getByTestId('job-status')).toContainText('running')
    expect(JSON.parse(applyBody)).toEqual({ terms: ['魏婴'] })
    expect(runs).toHaveLength(1)
    await expect(run.getByText('Glossary: Added 1.')).toBeVisible()
    await expect(page.getByTestId('glossary-review')).toHaveCount(0)
  })

  test('Review glossary first (no novel): uses the lines; Cancel starts nothing', async ({ page }) => {
    await base(page, { novel: false })
    const starts: string[] = []
    await mockRun(page, 'lines', [prop('魏婴', 'Wei Ying')], starts)
    let applied = false
    await page.route('**/api/glossary/dramas/1/from-lines/apply', (route) => {
      applied = true
      return route.fulfill({ json: { added: [], overwritten: [], skipped_existing: [], unknown: [] } })
    })
    const runs = await mockTranslateRun(page)

    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByLabel('Review glossary before translating').check()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review).toContainText("this drama's source lines")
    await expect(review.getByRole('button', { name: 'Add 1 term and start translation' })).toBeVisible()
    expect(starts).toEqual(['lines'])
    await review.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByTestId('glossary-review')).toHaveCount(0)
    expect(runs).toHaveLength(0)
    expect(applied).toBe(false)
  })

  test('Review glossary first: a paid-engine 403 still lets the run start without adding', async ({ page }) => {
    await base(page, { novel: false })
    await page.route('**/api/glossary/dramas/1/from-lines', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
        : notFound(route),
    )
    const runs = await mockTranslateRun(page)
    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByLabel('Review glossary before translating').check()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review.getByRole('alert').filter({ hasText: "This engine is paid and this account can't use it." })).toBeVisible()
    await review.getByRole('button', { name: 'Start translation' }).click()
    await expect.poll(() => runs.length).toBe(1)
  })

  test('Source stage: novel glossary sits under the novel panel and extracts', async ({ page }) => {
    await base(page, { novel: true })
    const starts: string[] = []
    await mockRun(page, 'novel', [prop('魏婴', 'Wei Ying')], starts)
    await page.goto('/#/drama/1/source')
    const region = page.getByRole('region', { name: 'Glossary from novel' })
    await region.locator('.section-title').click()
    const box = region.getByTestId('novel-glossary')
    await box.getByRole('button', { name: 'Extract terms' }).click()
    await expect(box.getByTestId('novel-glossary-proposals').locator('tbody tr')).toHaveCount(1)
    expect(starts).toEqual(['novel'])
    await shot(page, 'source-novel-glossary-desktop')
  })
})

test.describe('phone 390px', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  async function noSideScroll(page: Page) {
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  }

  test('From lines proposals are editable cards with one primary and no sideways scroll', async ({ page }) => {
    await base(page, { novel: false })
    await page.route('**/api/glossary/dramas/1/from-lines', (route) =>
      route.fulfill({
        json: {
          job_id: 'lines_glossary_1', status: 'done', progress: 1, message: '',
          proposals: [prop('魏婴', 'Wei Ying'), prop('云深不知处', 'Cloud Recesses'), prop('江澄', 'Jiang Cheng', true)],
        },
      }),
    )
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From lines')
    const box = page.getByTestId('lines-glossary')
    await expect(box.locator('ul.novel-glossary-cards > li')).toHaveCount(3)
    await expect(box.getByLabel('Translation for 云深不知处')).toBeVisible()
    await expect(box.locator('button.primary')).toHaveCount(1)
    await noSideScroll(page)
    await shot(page, 'glossary-from-lines-phone')
  })

  test('Source stage novel glossary fits the phone width', async ({ page }) => {
    await base(page, { novel: true })
    await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
      route.fulfill({
        json: { job_id: 'novel_glossary_1', status: 'done', progress: 1, message: '', proposals: [prop('魏婴', 'Wei Ying')] },
      }),
    )
    await page.goto('/#/drama/1/source')
    const region = page.getByRole('region', { name: 'Glossary from novel' })
    await region.locator('.section-title').click()
    await expect(region.locator('ul.novel-glossary-cards > li')).toHaveCount(1)
    await noSideScroll(page)
    await shot(page, 'source-novel-glossary-phone')
  })
})
