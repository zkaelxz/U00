import { expect, test, type Page } from '@playwright/test'

import { expectNoSidewaysScroll, topCards } from './mediaTabs'
import { gotoTranslate, translatePanel, translateTab, type TranslateTabName } from './translateTabs'

// The Translate stage is one screen per sub-tab, like the Media stage. Seeded titles: 1 audio drama, 2 novel, 3 video drama.

const TABS: TranslateTabName[] = ['translate', 'glossary', 'characters', 'novel']

// Bulk batches only shows for a title that has had a batch; one finished batch gives the fold without anything pending.
async function withFinishedBatch(page: Page) {
  await page.route('**/api/translate-run/dramas/1/bulk', (route) => route.fulfill({
    json: {
      drama_id: 1,
      jobs: [{
        bulk_job_id: 9, engine: 'claude', model: 'm', kind: 'translate', stage: null, pipeline_id: null,
        status: 'applied', pending: false, cancellable: false, line_count: 3, scheduled_for: null,
        result_summary: { applied: 3, missing: 0 }, last_error: null, submitted_at: '2026-09-29T09:00:00', updated_at: '2026-09-29T09:05:00',
      }],
    },
  }))
}

const tabNames = (page: Page) => page.getByRole('tablist', { name: 'Translate sections' }).getByRole('tab').allTextContents()

test('every title has Translate, Glossary, Characters and Reference novel', async ({ page }) => {
  for (const id of [1, 2, 3]) {
    await page.goto(`/#/drama/${id}/translate`)
    await expect(page.getByRole('tablist', { name: 'Translate sections' })).toBeVisible()
    expect(await tabNames(page), `title ${id}`).toEqual(['Translate', 'Glossary', 'Characters', 'Reference novel'])
  }
})

test('a title that was never opened lands on Translate', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await expect(translateTab(page, 'translate')).toHaveAttribute('aria-selected', 'true')
  await expect(translatePanel(page, 'translate').getByRole('button', { name: /^Translate( \d+ lines?)?$/ })).toBeVisible()
})

test('each tab holds its own panels', async ({ page }) => {
  await withFinishedBatch(page)
  await gotoTranslate(page, 'translate')
  const run = translatePanel(page, 'translate')
  await expect(run.getByRole('region', { name: 'Translate run' })).toBeVisible()
  await expect(run.locator('.section-title', { hasText: /^More options$/ })).toBeVisible()
  await expect(run.locator('.section-title', { hasText: /^Bulk batches$/ })).toBeVisible()
  await expect(run.getByRole('region', { name: 'Glossary' })).toHaveCount(0)

  await translateTab(page, 'glossary').click()
  const glossary = translatePanel(page, 'glossary')
  await expect(glossary.getByRole('region', { name: 'Glossary' })).toBeVisible()
  await expect(glossary.getByRole('tablist', { name: 'Glossary' })).toBeVisible()
  await expect(glossary.getByRole('button', { name: 'Translate', exact: true })).toHaveCount(0)

  await translateTab(page, 'characters').click()
  await expect(translatePanel(page, 'characters').getByRole('region', { name: 'Characters' })).toBeVisible()

  await translateTab(page, 'novel').click()
  await expect(translatePanel(page, 'novel').getByRole('region', { name: 'Novel reference (English translation)' })).toBeVisible()
})

test('the last sub-tab is remembered per title', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await translateTab(page, 'glossary').click()
  await page.goto('/#/drama/3/translate')
  await expect(translateTab(page, 'translate')).toHaveAttribute('aria-selected', 'true')
  await translateTab(page, 'novel').click()
  await page.goto('/#/drama/1/translate')
  await expect(translateTab(page, 'glossary')).toHaveAttribute('aria-selected', 'true')
  await page.goto('/#/drama/3/translate')
  await expect(translateTab(page, 'novel')).toHaveAttribute('aria-selected', 'true')
})

test('the address follows the tab, so a reload stays on it', async ({ page }) => {
  await page.goto('/#/drama/1/translate?tab=glossary')
  await translateTab(page, 'characters').click()
  await expect(page).toHaveURL(/#\/drama\/1\/translate\?tab=characters$/)
  await page.reload()
  await expect(translateTab(page, 'characters')).toHaveAttribute('aria-selected', 'true')
})

test('a link can open a sub-tab, and moving between tabs while open follows the link', async ({ page }) => {
  await gotoTranslate(page, 'glossary')
  await page.evaluate(() => { window.location.hash = '#/drama/1/translate?tab=novel' })
  await expect(translateTab(page, 'novel')).toHaveAttribute('aria-selected', 'true')
  await expect(translatePanel(page, 'novel').getByRole('region', { name: 'Novel reference (English translation)' })).toBeVisible()
})

test('an unknown tab, or one from the Media stage, falls back to Translate', async ({ page }) => {
  for (const tab of ['nope', 'tools']) {
    await page.goto(`/#/drama/3/translate?tab=${tab}`)
    await expect(translateTab(page, 'translate')).toHaveAttribute('aria-selected', 'true')
  }
})

test('the Media stage keeps its own tab link', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=credits')
  await expect(page.getByRole('tablist', { name: 'Media sections' }).getByRole('tab', { name: 'Credits' })).toHaveAttribute('aria-selected', 'true')
})

test('Edit in Translate links (?focus=) open the tab that holds the panel', async ({ page }) => {
  await page.goto('/#/drama/1/translate?focus=glossary')
  await expect(translateTab(page, 'glossary')).toHaveAttribute('aria-selected', 'true')
  await expect(translatePanel(page, 'glossary').getByRole('region', { name: 'Glossary' })).toBeVisible()
  await page.goto('/#/drama/1/translate?focus=characters')
  await expect(translateTab(page, 'characters')).toHaveAttribute('aria-selected', 'true')
  // A tab chosen after arriving wins over the focus left in the address.
  await translateTab(page, 'novel').click()
  await page.reload()
  await expect(translateTab(page, 'novel')).toHaveAttribute('aria-selected', 'true')
})

test('"Build the glossary from this novel in Translate" opens the Glossary tab', async ({ page }) => {
  await page.route('**/api/novel/dramas/2/status', (r) =>
    r.fulfill({ json: { drama_id: 2, has_novel_text: true, char_count: 1200, chapters: 3, ocr_running: false } }))
  await page.goto('/#/drama/2/source')
  await page.getByRole('link', { name: /Build the glossary from this novel in Translate/ }).click()
  await expect(page).toHaveURL(/#\/drama\/2\/translate\?tab=glossary/)
  await expect(translateTab(page, 'glossary')).toHaveAttribute('aria-selected', 'true')
})

test.describe('one open card per sub-tab', () => {
  test('fresh storage: Translate shows its form with the other cards folded, every other tab opens its one card', async ({ page }) => {
    await withFinishedBatch(page)
    await gotoTranslate(page, 'translate')
    await expect(translatePanel(page, 'translate').getByRole('region', { name: 'Translate run' })).toBeVisible()
    await expect.poll(() => topCards(translatePanel(page, 'translate'))).toEqual([
      { title: 'More options', open: false },
      { title: 'Bulk batches', open: false },
    ])
    const expected = { glossary: 'Glossary', characters: 'Characters', novel: 'Novel reference (English translation)' } as const
    for (const tab of ['glossary', 'characters', 'novel'] as const) {
      await translateTab(page, tab).click()
      expect(await topCards(translatePanel(page, tab)), tab).toEqual([{ title: expected[tab], open: true }])
    }
  })

  test('a batch still pending keeps Bulk batches open, since Start points to it', async ({ page }) => {
    await page.route('**/api/translate-run/dramas/1/bulk', (route) => route.fulfill({
      json: {
        drama_id: 1,
        jobs: [{
          bulk_job_id: 12, engine: 'claude', model: 'm', kind: 'translate', stage: null, pipeline_id: null,
          status: 'submitted', pending: true, cancellable: true, line_count: 3, scheduled_for: null,
          result_summary: null, last_error: null, submitted_at: '2026-09-29T09:00:00', updated_at: '2026-09-29T09:05:00',
        }],
      },
    }))
    await gotoTranslate(page, 'translate')
    await expect(translatePanel(page, 'translate').getByRole('region', { name: 'Bulk batches' })).toBeVisible()
    await expect(translatePanel(page, 'translate').getByRole('region', { name: 'Translate run' })).toBeVisible()
    await expect.poll(async () => (await topCards(translatePanel(page, 'translate'))).map((c) => c.open)).toEqual([false, true])
  })

  test('what the viewer folds and unfolds is kept', async ({ page }) => {
    await withFinishedBatch(page)
    await gotoTranslate(page, 'translate')
    const panel = translatePanel(page, 'translate')
    await panel.locator('summary', { hasText: 'Bulk batches' }).click()
    await expect(panel.getByRole('region', { name: 'Bulk batches' })).toBeVisible()
    await translateTab(page, 'characters').click()
    await translatePanel(page, 'characters').locator('details.section > summary').first().click()
    await page.reload()
    await expect.poll(() => topCards(translatePanel(page, 'characters'))).toEqual([{ title: 'Characters', open: false }])
    await translateTab(page, 'translate').click()
    await expect.poll(async () => (await topCards(translatePanel(page, 'translate'))).map((c) => c.open)).toEqual([false, true])
  })
})

test('a running job stays above the tabs on every tab', async ({ page }) => {
  await page.route('**/api/jobs/bulk_translate_1', (route) => route.fulfill({
    json: {
      job_id: 'bulk_translate_1', status: 'running', progress: 0.3, message: 'Waiting on the provider', error: null, description: null,
      gpu_touching: false, started_at: 1, finished_at: null, updated_at: Date.now() / 1000,
    },
  }))
  await page.goto('/#/drama/1/translate?tab=glossary')
  const job = page.getByRole('region', { name: 'Job' })
  for (const tab of TABS) {
    await translateTab(page, tab).click()
    await expect(job, tab).toBeVisible()
    const above = await page.evaluate(() => {
      const j = document.querySelector('[data-testid="job-panel"]')!.getBoundingClientRect()
      const t = document.querySelector('[role="tablist"][aria-label="Translate sections"]')!.getBoundingClientRect()
      return j.bottom <= t.top
    })
    expect(above, `${tab}: job above the tab row`).toBe(true)
  }
})

test('each sub-tab fits about one desktop screen and does not scroll sideways', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const tab of TABS) {
    await gotoTranslate(page, tab)
    await expect(translatePanel(page, tab)).toBeVisible()
    await expectNoSidewaysScroll(page, tab)
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    expect(height, `${tab}: page height`).toBeLessThanOrEqual(800 * 1.25)
  }
})

for (const scheme of ['light', 'dark'] as const) {
  test(`no sub-tab scrolls sideways at 1280x800 with every card open (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await page.setViewportSize({ width: 1280, height: 800 })
    for (const tab of TABS) {
      await gotoTranslate(page, tab)
      const folds = translatePanel(page, tab).locator('details.section')
      for (let i = 0; i < (await folds.count()); i++) {
        const fold = folds.nth(i)
        if ((await fold.getAttribute('open')) === null) await fold.locator(':scope > summary').click()
      }
      await expectNoSidewaysScroll(page, `${tab} ${scheme}`)
    }
  })
}
