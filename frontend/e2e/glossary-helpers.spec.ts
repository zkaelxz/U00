import { expect, test, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'
import { withTranslateLines } from './stageLineMocks'

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
  // Some sections now start open; click only a closed one, as a user would.
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
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

// A mocked extraction route: 404 until started (or an earlier finished
// run-0 when `earlier` is given), then each start is run-N: running for one
// read, then done with the next entry of `runs` (the last one repeats).
// Like the server, a new run's id is readable as soon as the start answers.
function mockRun(
  page: Page,
  kind: 'novel' | 'lines',
  proposals: object[] | object[][],
  starts: string[],
  earlier?: object[],
) {
  const runs = (Array.isArray(proposals[0]) ? proposals : [proposals]) as object[][]
  const job = `${kind}_glossary_1`
  const run = { n: 0, state: (earlier ? 'done' : 'none') as 'none' | 'running' | 'done' }
  const done = () => runs[Math.min(run.n, runs.length) - 1] ?? earlier ?? []
  const body = () =>
    run.state === 'running'
      ? { job_id: job, status: 'running', progress: 0.1, message: '', proposals: null, run_id: `run-${run.n}` }
      : { job_id: job, status: 'done', progress: 1, message: '', proposals: done(), run_id: `run-${run.n}` }
  return page
    .route(`**/api/glossary/dramas/1/from-${kind}`, (route) => {
      if (route.request().method() === 'POST') {
        starts.push(kind)
        run.n += 1
        run.state = 'running'
        return route.fulfill({ json: kind === 'lines' ? { job_id: job, engine: 'claude', line_count: 12 } : { job_id: job, engine: 'claude', paired: false } })
      }
      if (run.state === 'none') return notFound(route)
      const json = body()
      if (run.state === 'running') run.state = 'done'
      return route.fulfill({ json })
    })
    .then(() => run)
}

async function mockTranslateRun(page: Page) {
  const bodies: unknown[] = []
  await withTranslateLines(page)
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
      run_id: 'run-1',
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
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
    await expect(run.getByRole('switch', { name: 'Review glossary before translating' })).toHaveAttribute('aria-checked', 'true')
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
    expect(JSON.parse(applyBody)).toEqual({ terms: ['魏婴'], run_id: 'run-1' })
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
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
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

  test('Review glossary first: Cancel during a run cancels that run by run_id only', async ({ page }) => {
    await base(page, { novel: false })
    let started = false
    await page.route('**/api/glossary/dramas/1/from-lines', (route) => {
      if (route.request().method() === 'POST') {
        started = true
        return route.fulfill({ json: { job_id: 'lines_glossary_1', engine: 'claude', line_count: 12 } })
      }
      if (!started) return notFound(route)
      return route.fulfill({ json: { job_id: 'lines_glossary_1', status: 'running', progress: 0.1, message: '', proposals: null, run_id: 'run-7' } })
    })
    const cancels: unknown[] = []
    await page.route('**/api/glossary/dramas/1/from-lines/cancel', (route) => {
      cancels.push(route.request().postDataJSON())
      return route.fulfill({ json: { job_id: 'lines_glossary_1', cancel_requested: true, status: 'running' } })
    })
    let jobCancel = false
    await page.route('**/api/jobs/*/cancel', (route) => {
      jobCancel = true
      return route.fulfill({ json: { job_id: 'x', cancel_requested: true, status: 'running' } })
    })
    const runs = await mockTranslateRun(page)

    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review).toBeVisible()
    await expect.poll(() => started).toBe(true)
    await review.getByRole('button', { name: 'Cancel' }).click()
    await expect.poll(() => cancels).toEqual([{ run_id: 'run-7' }])
    expect(jobCancel).toBe(false)
    expect(runs).toHaveLength(0)
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
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review.getByRole('alert').filter({ hasText: "This engine is paid and this account can't use it." })).toBeVisible()
    await review.getByRole('button', { name: 'Start translation' }).click()
    await expect.poll(() => runs.length).toBe(1)
  })

  test('Review glossary first never shows an earlier finished run', async ({ page }) => {
    await base(page, { novel: false })
    const starts: string[] = []
    // A finished run-0 is held from before (say, another tab); pressing
    // Translate starts run-1, and only run-1's proposals may be reviewed.
    await mockRun(page, 'lines', [prop('魏婴', 'Wei Ying')], starts, [prop('旧词', 'Old term')])
    let applyBody = ''
    await page.route('**/api/glossary/dramas/1/from-lines/apply', (route) => {
      applyBody = route.request().postData() ?? ''
      return route.fulfill({ json: { added: ['魏婴'], overwritten: [], skipped_existing: [], unknown: [] } })
    })
    const runs = await mockTranslateRun(page)
    await page.goto('/#/drama/1/translate')
    // Record whether the earlier run's term ever renders in the review.
    await page.evaluate(() => {
      const w = window as unknown as { sawEarlier: boolean }
      w.sawEarlier = false
      new MutationObserver(() => {
        const review = document.querySelector('[data-testid="glossary-review"]')
        if (review?.textContent?.includes('旧词')) w.sawEarlier = true
      }).observe(document.body, { subtree: true, childList: true, characterData: true })
    })
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    const review = page.getByTestId('glossary-review')
    await expect(review.getByTestId('glossary-review-proposals').locator('tbody tr')).toHaveCount(1)
    await expect(review).toContainText('魏婴')
    expect(starts).toEqual(['lines'])
    await review.getByRole('button', { name: 'Add 1 term and start translation' }).click()
    await expect.poll(() => runs.length).toBe(1)
    expect(JSON.parse(applyBody)).toEqual({ terms: ['魏婴'], run_id: 'run-1' })
    expect(await page.evaluate(() => (window as unknown as { sawEarlier: boolean }).sawEarlier)).toBe(false)
  })

  test('From lines: a new run started elsewhere resets the selection and edits', async ({ page }) => {
    await base(page, { novel: false })
    const starts: string[] = []
    await mockRun(
      page,
      'lines',
      [
        [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan')],
        [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan'), prop('江澄', 'Jiang Cheng')],
      ],
      starts,
    )
    await mockTranslateRun(page)
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From lines')
    const box = page.getByTestId('lines-glossary')
    await box.getByRole('button', { name: 'Extract terms' }).click()
    await expect(box.getByTestId('lines-glossary-proposals').locator('tbody tr')).toHaveCount(2)
    await box.getByLabel('Select 蓝湛').uncheck()
    await box.getByLabel('Policy for 魏婴').selectOption('hybrid')
    await expect(box.getByRole('button', { name: 'Add 1 term to series glossary' })).toBeVisible()

    // The review before translating starts run-2 while the panel stays mounted.
    const run = page.getByRole('region', { name: 'Translate run' })
    await run.getByRole('switch', { name: 'Review glossary before translating' }).click()
    await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
    await expect(page.getByTestId('glossary-review-proposals').locator('tbody tr')).toHaveCount(3)
    expect(starts).toEqual(['lines', 'lines'])

    await expect(box.getByTestId('lines-glossary-proposals').locator('tbody tr')).toHaveCount(3)
    await expect(box.getByLabel('Select 蓝湛')).toBeChecked()
    await expect(box.getByLabel('Select 江澄')).toBeChecked()
    await expect(box.getByLabel('Policy for 魏婴')).toHaveValue('keep_pinyin')
    await expect(box.getByRole('button', { name: 'Add 3 terms to series glossary' })).toBeVisible()
  })

  test('From lines: a run replaced in another tab is refused (409) and shown to review again', async ({ page }) => {
    await base(page, { novel: false })
    const starts: string[] = []
    const lines = await mockRun(
      page,
      'lines',
      [[prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan')], [prop('江澄', 'Jiang Cheng')]],
      starts,
    )
    const applies: { run_id?: string }[] = []
    await page.route('**/api/glossary/dramas/1/from-lines/apply', (route) => {
      const body = route.request().postDataJSON() as { run_id?: string }
      applies.push(body)
      return body.run_id === `run-${lines.n}`
        ? route.fulfill({ json: { added: ['江澄'], overwritten: [], skipped_existing: [], unknown: [] } })
        : route.fulfill({
            status: 409,
            json: { error: { code: 'conflict', message: 'The proposals changed since you reviewed them — review again.' } },
          })
    })
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From lines')
    const box = page.getByTestId('lines-glossary')
    await box.getByRole('button', { name: 'Extract terms' }).click()
    await expect(box.getByTestId('lines-glossary-proposals').locator('tbody tr')).toHaveCount(2)
    await box.getByLabel('Select 蓝湛').uncheck()
    await box.getByLabel('Translation for 魏婴').fill('Wei Wuxian')

    // Another tab extracts again (the done panel doesn't poll, so it can't know).
    lines.n += 1
    lines.state = 'done'
    await box.getByRole('button', { name: 'Add 1 term to series glossary' }).click()
    await expect(box.getByRole('alert').filter({ hasText: 'The proposals changed since you reviewed them — review again.' })).toBeVisible()
    expect(applies).toEqual([{ terms: ['魏婴'], overrides: { 魏婴: { translation: 'Wei Wuxian' } }, run_id: 'run-1' }])
    // The panel re-reads and shows the newer run, with a fresh selection.
    await expect(box.getByTestId('lines-glossary-proposals').locator('tbody tr')).toHaveCount(1)
    await expect(box.getByLabel('Select 江澄')).toBeChecked()
    await box.getByRole('button', { name: 'Add 1 term to series glossary' }).click()
    await expect(box).toContainText('Added 1.')
    expect(applies[1]).toEqual({ terms: ['江澄'], run_id: 'run-2' })
    expect(starts).toEqual(['lines'])
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

  test('Review glossary toggle row fits the phone width', async ({ page }) => {
    await base(page, { novel: false })
    await page.route('**/api/glossary/dramas/1/from-lines', notFound)
    await page.goto('/#/drama/1/translate')
    const toggle = page.getByRole('region', { name: 'Translate run' }).getByRole('switch', { name: 'Review glossary before translating' })
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    const box = await toggle.boundingBox()
    expect(box && box.x + box.width).toBeLessThanOrEqual(390)
    await noSideScroll(page)
    await shot(page, 'translate-review-toggle-phone')
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
