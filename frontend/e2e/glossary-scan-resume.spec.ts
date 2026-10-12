import { expect, test, type Page } from '@playwright/test'
import { glossaryTab } from './glossaryTabs'
import { glossarySetup, heldScan, prop } from './glossaryScanMocks'

// A glossary scan outlives the page that started it: leaving, coming back, a reload or a
// toggle that is off must still show it. The server's scan routes are mocked.

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const glossary = (page: Page) => page.getByRole('region', { name: 'Glossary' })
const banner = (page: Page) => page.getByTestId('glossary-scan-banner')
const REVIEW = 'Review glossary before translating'

// The review option lives in More options, which starts folded.
async function reviewSwitch(page: Page) {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^More options$/ }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  return page.getByRole('switch', { name: REVIEW })
}

async function mock(page: Page, sources: { lines?: ReturnType<typeof heldScan>; novel?: ReturnType<typeof heldScan> }, opts: Parameters<typeof glossarySetup>[1] = {}) {
  await glossarySetup(page, opts)
  const lines = sources.lines ?? heldScan()
  const novel = sources.novel ?? heldScan()
  await page.route('**/api/glossary/dramas/1/from-lines', lines.handler)
  await page.route('**/api/glossary/dramas/1/from-novel', novel.handler)
  return { lines, novel }
}

test('a running scan is still shown after leaving the page and coming back', async ({ page }) => {
  const { lines } = await mock(page, {})
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Find terms')
  await page.getByRole('region', { name: 'Glossary' }).getByRole('button', { name: 'Suggest more terms' }).click()
  expect(lines.state.posts).toBe(1)
  lines.state.held = { status: 'running' }
  await expect(page.getByTestId('lines-glossary-running')).toBeVisible()

  await page.goto('/#/library')
  await expect(page.getByRole('heading', { name: 'Library' }).first()).toBeVisible()
  await page.goto('/#/drama/1/translate')

  await expect(banner(page)).toContainText('A glossary scan is running')
  // The remembered tab is Find terms, and it reads the scan, not "idle".
  await expect(page.getByTestId('lines-glossary-running')).toBeVisible()
  await expect(glossary(page).getByRole('tab', { name: /Find terms/ })).toContainText('1')
  expect(lines.state.posts).toBe(1)
})

test('a scan that finished while away says so and opens its proposals', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'done', progress: 1, proposals: [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan')] }) })
  await page.goto('/#/drama/1/translate')
  await expect(banner(page)).toContainText('Scan finished: 2 proposals to review')
  await expect(await reviewSwitch(page)).toHaveAttribute('aria-checked', 'false')
  await banner(page).getByRole('button', { name: 'Review proposals' }).click()
  const review = page.getByTestId('glossary-review')
  await expect(review.getByTestId('glossary-review-proposals').locator('tbody tr')).toHaveCount(2)
  await expect(review.getByRole('button', { name: 'Add 2 terms and start translation' })).toBeVisible()
  await expect(banner(page)).toHaveCount(0)
})

test('the same finished scan survives a reload, on the Glossary tab too', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'done', progress: 1, proposals: [prop('魏婴', 'Wei Ying')] }) })
  await page.goto('/#/drama/1/translate')
  await expect(banner(page)).toContainText('Scan finished: 1 proposal to review')
  await page.reload()
  await expect(banner(page)).toContainText('Scan finished: 1 proposal to review')
  await glossaryTab(page, 'Find terms')
  await expect(page.getByTestId('lines-glossary-proposals').locator('tbody tr')).toHaveCount(1)
  await expect(glossary(page).getByRole('tab', { name: /Find terms/ })).toContainText('1')
})

test("the other source's idle status never hides a running scan", async ({ page }) => {
  // An audio title defaults to the transcript, but its novel is the one being scanned.
  await mock(page, { novel: heldScan({ status: 'running' }) }, { novel: true })
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Find terms')
  await expect(page.getByRole('combobox', { name: 'Suggest terms from' })).toHaveValue('novel')
  await expect(page.getByTestId('novel-glossary-running')).toBeVisible()
  await expect(banner(page)).toContainText('A glossary scan is running')
  await page.reload()
  await expect(page.getByTestId('novel-glossary-running')).toBeVisible()
})

test('the chosen source is remembered for the title', async ({ page }) => {
  await mock(page, {}, { novel: true })
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Find terms')
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker).toHaveValue('lines')
  await picker.selectOption({ label: 'Novel' })
  await page.reload()
  await expect(page.getByRole('combobox', { name: 'Suggest terms from' })).toHaveValue('novel')
})

test('a scan that finishes while the window is in the background is picked up on return', async ({ page }) => {
  const { lines } = await mock(page, { lines: heldScan({ status: 'running' }) })
  await page.goto('/#/drama/1/translate')
  await expect(banner(page)).toContainText('A glossary scan is running')
  lines.state.held = { status: 'done', progress: 1, proposals: [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan'), prop('江澄', 'Jiang Cheng')] }
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(banner(page)).toContainText('Scan finished: 3 proposals to review')
})

test('a folded Glossary section still says a scan is waiting', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'done', progress: 1, proposals: [prop('魏婴', 'Wei Ying')] }) })
  await page.addInitScript(() => localStorage.setItem('baihe.section.translate.glossary', '0'))
  await page.goto('/#/drama/1/translate')
  const fold = page.locator('details.section', { has: page.locator('.section-title', { hasText: /^Glossary$/ }) }).first()
  await expect(fold.locator(':scope > summary .section-summary')).toContainText('scan finished, 1 to review')
})

test('a failed scan is reported with a way to see it', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'error', message: 'engine unreachable' }) })
  await page.goto('/#/drama/1/translate')
  await expect(banner(page)).toContainText('The glossary scan failed')
  await banner(page).getByRole('button', { name: 'See details' }).click()
  await expect(page.getByTestId('glossary-review').getByRole('alert')).toBeVisible()
})

test('no banner when nothing is held, or the scan found nothing', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'done', progress: 1, proposals: [] }) })
  await page.goto('/#/drama/1/translate')
  await expect(glossary(page).getByRole('tab', { name: 'Terms', exact: true })).toBeVisible()
  await expect(banner(page)).toHaveCount(0)
})

test('the review option on at load still opens the held scan by itself', async ({ page }) => {
  await mock(page, { lines: heldScan({ status: 'running' }) })
  await page.goto('/#/drama/1/translate')
  await (await reviewSwitch(page)).click()
  await page.reload()
  await expect(page.getByTestId('glossary-review-running')).toContainText('Scanning the transcript')
})

// --- Suggest spellings: one paid model call, its result stays on the server ---

const SPELLING_DONE = {
  job_id: 'spelling_suggest_1', status: 'done', progress: 1, message: '', run_id: 'sp-1',
  result: {
    term: 'ヤロ', english: 'Yaro', lines_scanned: 40, truncated: false, unusable: false,
    proposals: [{ text: 'ヤロック', count: 2, score: 0.8, is_alias: false, is_term: false, examples: [{ line_idx: 4, snippet: 'ヤロックはどこだ' }] }],
  },
}

test('Suggest spellings comes back with its result and the English name after leaving or reloading', async ({ page }) => {
  await mock(page, {})
  let ran = false
  await page.route('**/api/glossary/dramas/1/spelling-suggestions', (route) => {
    if (route.request().method() === 'POST') {
      ran = true
      return route.fulfill({ json: { job_id: 'spelling_suggest_1', engine: 'claude' } })
    }
    return route.fulfill({ json: ran ? SPELLING_DONE : { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null } })
  })
  await page.goto('/#/drama/1/translate')
  await glossary(page).getByRole('button', { name: 'Suggest spellings of ヤロ' }).click()
  const panel = glossary(page).getByRole('region', { name: 'Suggest spellings of ヤロ' })
  await panel.getByRole('textbox', { name: 'Name in English' }).fill('Yaro')
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel).toContainText('ヤロック')

  for (const leave of ['reload', 'away'] as const) {
    if (leave === 'reload') await page.reload()
    else {
      await page.goto('/#/library')
      await expect(page.getByRole('heading', { name: 'Library' }).first()).toBeVisible()
      await page.goto('/#/drama/1/translate')
    }
    await glossaryTab(page, 'Names')
    const again = glossary(page).getByRole('region', { name: 'Suggest spellings of ヤロ' })
    await expect(again).toContainText('ヤロック')
    await expect(again.getByRole('textbox', { name: 'Name in English' })).toHaveValue('Yaro')
  }
})

test('Suggest spellings reopens from the server alone, with no memory in the browser', async ({ page }) => {
  await mock(page, {})
  await page.route('**/api/glossary/dramas/1/spelling-suggestions', (route) => route.fulfill({ json: SPELLING_DONE }))
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Names')
  await expect(glossary(page).getByRole('region', { name: 'Suggest spellings of ヤロ' })).toContainText('ヤロック')
})

test('an idle spelling status opens nothing', async ({ page }) => {
  await mock(page, {})
  await page.route('**/api/glossary/dramas/1/spelling-suggestions', (route) =>
    route.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null } }),
  )
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Names')
  await expect(glossary(page).getByRole('region', { name: 'Suggest spellings of ヤロ' })).toHaveCount(0)
})

test('the all-titles name check stays open across a reload', async ({ page }) => {
  await mock(page, {})
  await page.route('**/api/glossary/name-report', (route) => route.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', result: null } }))
  await page.goto('/#/drama/1/translate')
  await glossaryTab(page, 'Names')
  await glossary(page).getByRole('button', { name: 'Check names across all titles' }).click()
  await expect(glossary(page).getByRole('region', { name: 'Name check across all titles' })).toBeVisible()
  await page.reload()
  await expect(glossary(page).getByRole('region', { name: 'Name check across all titles' })).toBeVisible()
})
