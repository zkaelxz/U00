import { expect, test, type Page } from '@playwright/test'

// "Suggest spellings" and "Check English names" on the Glossary panel. The seeded dramas have
// no series, so terms and the spelling routes are mocked; nothing is saved except through the
// mocked term route, and only after the confirm.

const TERMS = [
  { id: 11, term_original: 'ヤロ', term_translation: 'Yalo', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] },
]
const BASE = '**/api/glossary/dramas/1/spelling-suggestions'

const done = (over = {}) => ({
  job_id: 'spelling_suggest_1', status: 'done', progress: 1, message: '', run_id: 'r1',
  result: {
    term: 'ヤロ', english: 'Yalo', unusable: false, lines_scanned: 120, truncated: false,
    proposals: [{ text: 'ヤロック', count: 3, is_alias: false, is_term: false, examples: [{ line_idx: 4, snippet: 'ヤロックはどこだ' }] }],
    ...over,
  },
})

async function openPanel(page: Page, mockTerms = true) {
  if (mockTerms) await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: TERMS }))
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByRole('tab', { name: 'Terms', exact: true })).toBeVisible()
  await glossary.getByRole('button', { name: 'Suggest spellings of ヤロ' }).click()
  return glossary.getByRole('region', { name: 'Suggest spellings of ヤロ' })
}

// Idle until a start is posted, then the given status.
async function mockRun(page: Page, after: object, started: unknown[] = []) {
  let ran = false
  await page.route(BASE, (route) => {
    if (route.request().method() === 'POST') {
      started.push(route.request().postDataJSON())
      ran = true
      return route.fulfill({ json: { job_id: 'spelling_suggest_1', engine: 'claude' } })
    }
    return route.fulfill({
      json: ran ? after : { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null },
    })
  })
}

test('suggests spellings from the English name and adds one only after confirming', async ({ page }) => {
  const started: unknown[] = []
  await mockRun(page, done(), started)
  const saved: unknown[] = []
  await page.route('**/api/glossary/dramas/1/terms', (route) => {
    if (route.request().method() === 'POST') {
      saved.push(route.request().postDataJSON())
      return route.fulfill({ json: { ...TERMS[0], aliases: ['ヤロック'] } })
    }
    return route.fulfill({ json: TERMS })
  })
  const panel = await openPanel(page, false)
  await expect(panel.getByRole('textbox', { name: 'Name in English' })).toHaveValue('Yalo')
  await panel.getByRole('textbox', { name: 'Name in English' }).fill('Yaro')
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel).toContainText('ヤロック')
  await expect(panel).toContainText('3 mentions in the lines')
  await expect(panel).toContainText('#5 ヤロックはどこだ')
  expect(started).toEqual([{ term_id: 11, english: 'Yaro' }])
  expect(saved).toHaveLength(0)

  await panel.getByRole('button', { name: 'Add ヤロック as alias' }).click()
  await panel.getByRole('button', { name: 'Cancel' }).click()
  expect(saved).toHaveLength(0)
  await panel.getByRole('button', { name: 'Add ヤロック as alias' }).click()
  await panel.getByRole('button', { name: 'Yes, add alias' }).click()
  await expect(panel.getByText('Added as an alias.')).toBeVisible()
  expect(saved).toEqual([{ id: 11, aliases: ['ヤロック'] }])
})

test('an unusable model answer says so plainly and lists nothing', async ({ page }) => {
  await mockRun(page, done({ unusable: true, proposals: [] }))
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel).toContainText("The model's answer could not be used")
  await expect(panel.getByRole('button', { name: /as alias/ })).toHaveCount(0)
})

test('guesses that never occur show the empty state', async ({ page }) => {
  await mockRun(page, done({ proposals: [] }))
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel).toContainText("None of the model's guesses appear in this title's lines.")
})

test('a spent monthly cap is shown as the reason, with nothing started', async ({ page }) => {
  await page.route(BASE, (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 400, json: { error: { code: 'unsupported_operation', message: "This month's spending cap is used up." } } })
    : route.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null } }))
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('spending cap')
  await expect(panel.getByRole('button', { name: 'Suggest spellings', exact: true })).toBeEnabled()
})

test('a running suggestion can be cancelled', async ({ page }) => {
  let cancelled = false
  let ran = false
  await page.route(BASE, (route) => {
    if (route.request().method() === 'POST') {
      ran = true
      return route.fulfill({ json: { job_id: 'spelling_suggest_1', engine: 'claude' } })
    }
    return route.fulfill({ json: !ran
      ? { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null }
      : cancelled
      ? { job_id: 'spelling_suggest_1', status: 'cancelled', progress: 0.1, message: '', run_id: 'r1', result: null }
      : { job_id: 'spelling_suggest_1', status: 'running', progress: 0.1, message: 'Asking the model for likely spellings...', run_id: 'r1', result: null } })
  })
  await page.route(`${BASE}/cancel`, (route) => {
    cancelled = true
    return route.fulfill({ json: { job_id: 'spelling_suggest_1', cancel_requested: true } })
  })
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await expect(panel).toContainText('Asking the model')
  await panel.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(panel).toContainText('Cancelled. Nothing was changed.')
})

test('the English name check lists lines that render the name differently', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/name-consistency?*', (route) => route.fulfill({
    json: {
      term: 'ヤロ', english: 'Yalo', available: true, reason: '', lines_with_name: 4, untranslated: 1, consistent: 1,
      differing: [{ line_idx: 2, source: 'ヤロが来た', english: 'Yaloch came' }, { line_idx: 3, source: 'ヤロも来た', english: 'Yalou came too' }],
      other_words: ['Yaloch', 'Yalou'], lines_scanned: 4, truncated: true, more_rows: false,
    },
  }))
  await page.route(BASE, (route) => route.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null } }))
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Check English names' }).click()
  const check = panel.getByRole('region', { name: 'English name check' })
  await expect(check).toContainText('2 of 4 lines do not use “Yalo”')
  await expect(check).toContainText('Yaloch came')
  await expect(check).toContainText('Only the first 4 lines were checked.')
  await expect(check).toContainText('Other capitalised words in those lines: Yaloch, Yalou.')
})
