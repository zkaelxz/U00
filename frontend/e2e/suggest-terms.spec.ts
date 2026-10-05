import { expect, test, type Page, type Route } from '@playwright/test'
import { withExportLines } from './stageLineMocks'
import { suggestFrom } from './suggestTerms'

// Glossary > Suggest terms bar and the empty-state card. Drama reads hit the
// real seeded API (drama 1, reported as in series 7); terms, novel status and
// the extraction runs are mocked, so no engine is ever called.

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const IDLE = { job_id: '', status: 'idle', progress: 0, message: '', proposals: null, run_id: null }
const TERM = { id: 1, term_original: '魏婴', term_translation: 'Wei Ying', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }

interface Setup {
  terms?: object[]
  novel?: boolean
  lines?: boolean
}

async function setup(page: Page, { terms = [], novel = false, lines = true }: Setup = {}) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: terms }))
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: novel, char_count: novel ? 900 : 0, chapters: novel ? 3 : 0, ocr_running: false } }),
  )
  await withExportLines(page, 1, lines ? 3 : 0)
}

// A lines run: idle until started, then running.
async function mockLinesRun(page: Page) {
  const starts: string[] = []
  await page.route('**/api/glossary/dramas/1/from-lines', (route: Route) => {
    if (route.request().method() === 'POST') {
      starts.push('lines')
      return route.fulfill({ json: { job_id: 'lines_glossary_1', engine: 'claude', line_count: 3 } })
    }
    return route.fulfill({ json: starts.length ? { ...IDLE, job_id: 'lines_glossary_1', status: 'running', run_id: 'run-1' } : IDLE })
  })
  return starts
}

const bar = (page: Page) => page.getByRole('region', { name: 'Glossary' }).locator('.suggest-bar')

test('the bar sits at the top of the Glossary section with its help line', async ({ page }) => {
  await setup(page, { terms: [TERM] })
  await page.goto('/#/drama/1/translate')
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker).toBeVisible()
  await expect(page.getByText('Uses your translation engine. You review the suggestions before anything is added.')).toBeVisible()
  const table = page.locator('table.glossary-table')
  await expect(table).toBeVisible()
  const barBox = await bar(page).boundingBox()
  const tableBox = await table.boundingBox()
  expect(barBox!.y).toBeLessThan(tableBox!.y)
  await expect(page.getByRole('button', { name: 'Suggest more terms' })).toBeVisible()
  await expect(page.getByTestId('glossary-start')).toHaveCount(0)
})

test('an empty glossary shows the start card with three actions', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  const card = page.getByTestId('glossary-start')
  await expect(card.getByRole('heading', { name: 'Start your glossary' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Suggest terms from the transcript' })).toBeEnabled()
  await expect(card.getByRole('button', { name: 'Add a term' })).toBeVisible()
  await expect(card.getByRole('button', { name: 'Import a file' })).toBeVisible()
  // Side by side on desktop.
  const ys = await card.getByRole('button').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(ys).size).toBe(1)
  // The bar button reads "Suggest terms" while there is nothing yet.
  await expect(bar(page).getByRole('button', { name: 'Suggest terms', exact: true })).toBeVisible()
})

test('Add a term opens the editor and Import a file opens the import fold', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  const card = page.getByTestId('glossary-start')
  await card.getByRole('button', { name: 'Import a file' }).click()
  await expect(page.locator('details.glossary-import')).toHaveAttribute('open', '')
  await card.getByRole('button', { name: 'Add a term' }).click()
  await expect(page.getByRole('group', { name: 'Add term' })).toBeVisible()
  await expect(page.getByTestId('glossary-start')).toHaveCount(0)
})

test('the card starts the transcript run and the bar shows its progress', async ({ page }) => {
  await setup(page)
  const starts = await mockLinesRun(page)
  await page.goto('/#/drama/1/translate')
  await page.getByTestId('glossary-start').getByRole('button', { name: 'Suggest terms from the transcript' }).click()
  await expect(page.getByTestId('lines-glossary-running')).toContainText('Scanning the transcript…')
  expect(starts).toEqual(['lines'])
})

test('the bar button starts a run from the picked source', async ({ page }) => {
  await setup(page, { terms: [TERM] })
  const starts = await mockLinesRun(page)
  await page.goto('/#/drama/1/translate')
  await bar(page).getByRole('button', { name: 'Suggest more terms' }).click()
  await expect(page.getByTestId('lines-glossary-running')).toBeVisible()
  expect(starts).toEqual(['lines'])
})

test('the dropdown offers both sources when the title has both, starting on the transcript for audio', async ({ page }) => {
  await setup(page, { terms: [TERM], novel: true })
  await page.goto('/#/drama/1/translate')
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker.locator('option')).toHaveText(['Transcript', 'Novel'])
  await expect(picker.locator('option:disabled')).toHaveCount(0)
  await expect(picker).toHaveValue('lines')
  await suggestFrom(page, 'Novel')
  await expect(page.getByTestId('novel-glossary')).toBeVisible()
})

test('only the available source is enabled, with the reason for the other', async ({ page }) => {
  await setup(page, { terms: [TERM] })
  await page.goto('/#/drama/1/translate')
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker.locator('option', { hasText: 'Novel' })).toBeDisabled()
  await expect(page.getByTestId('suggest-reason-novel')).toContainText('Still needed: novel text')
  await expect(page.getByTestId('suggest-reason-novel').getByRole('link', { name: 'attach it on Source' })).toHaveAttribute('href', '#/drama/1/source')
})

test('a title with only a novel starts on the novel and the card offers it', async ({ page }) => {
  await setup(page, { novel: true, lines: false })
  await page.goto('/#/drama/1/translate')
  await expect(page.getByRole('combobox', { name: 'Suggest terms from' })).toHaveValue('novel')
  await expect(page.getByTestId('suggest-reason-lines')).toContainText('Still needed: transcript lines')
  await expect(page.getByTestId('glossary-start').getByRole('button', { name: 'Suggest terms from the novel' })).toBeEnabled()
})
