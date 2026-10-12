import { expect, test, type Page } from '@playwright/test'

// The Glossary section starts folded the first time; these tests need its body.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.section.translate.glossary', '1'))
})

// "Find similar spellings" and "Possible names" on the Translate stage's Glossary
// panel. The seeded dramas have no series, so terms and both scans are mocked;
// nothing is saved except through the mocked term route.

const TERMS = [
  { id: 11, term_original: 'ヤロ', term_translation: 'Yalo', notes: '', category: null, policy: null, enforce_exact: false, aliases: ['ヤロー'], banned_translations: [] },
]

const VARIANTS = {
  term: 'ヤロ', available: true, reason: '', lines_scanned: 120, truncated: false,
  candidates: [
    { text: 'ヤロー', count: 2, score: 1, is_alias: true, is_term: false, examples: [{ line_idx: 0, snippet: 'ヤローが来た' }] },
    { text: 'ヤロック', count: 1, score: 0.83, is_alias: false, is_term: false, examples: [{ line_idx: 4, snippet: 'ヤロックはどこだ' }] },
  ],
}

async function openGlossary(page: Page, mockTerms = true) {
  if (mockTerms) await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: TERMS }))
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  return glossary
}

test('lists similar spellings and adds one as an alias only after confirming', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/variants?*', (route) => route.fulfill({ json: VARIANTS }))
  const saved: unknown[] = []
  await page.route('**/api/glossary/dramas/1/terms', (route) => {
    if (route.request().method() === 'POST') {
      saved.push(route.request().postDataJSON())
      return route.fulfill({ json: { ...TERMS[0], aliases: ['ヤロー', 'ヤロック'] } })
    }
    return route.fulfill({ json: TERMS })
  })
  const glossary = await openGlossary(page, false)
  await glossary.getByRole('button', { name: 'Find similar spellings of ヤロ' }).click()
  const panel = glossary.getByRole('region', { name: 'Similar spellings of ヤロ' })
  await expect(panel).toContainText('ヤロック')
  await expect(panel).toContainText('#5 ヤロックはどこだ')
  await expect(panel.getByText('Already an alias of this term.')).toBeVisible()

  await panel.getByRole('button', { name: 'Add ヤロック as alias' }).click()
  expect(saved).toHaveLength(0)
  await panel.getByRole('button', { name: 'Cancel' }).click()
  expect(saved).toHaveLength(0)

  await panel.getByRole('button', { name: 'Add ヤロック as alias' }).click()
  await panel.getByRole('button', { name: 'Yes, add alias' }).click()
  await expect(panel.getByText('Added as an alias.')).toBeVisible()
  expect(saved).toEqual([{ id: 11, aliases: ['ヤロー', 'ヤロック'] }])
})

test('says what to install when the reading library is missing', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/variants?*', (route) => route.fulfill({
    json: { ...VARIANTS, available: false, reason: 'Japanese kanji readings need the pykakasi package (pip install pykakasi).', candidates: [] },
  }))
  await page.route('**/api/glossary/dramas/1/name-clusters', (route) => route.fulfill({
    json: { available: false, reason: 'Japanese kanji readings need the pykakasi package (pip install pykakasi).', lines_scanned: 0, truncated: false, clusters: [], known_vtuber_names: [] },
  }))
  const glossary = await openGlossary(page)
  await glossary.getByRole('button', { name: 'Find similar spellings of ヤロ' }).click()
  await expect(glossary.getByRole('region', { name: 'Similar spellings of ヤロ' })).toContainText('pykakasi')
  await glossary.getByRole('button', { name: 'Find possible names' }).click()
  await expect(glossary.getByRole('region', { name: 'Possible names' })).toContainText('pykakasi')
})

test('possible names: a cluster can be named, but needs a series first', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/name-clusters', (route) => route.fulfill({
    json: {
      available: true, reason: '', lines_scanned: 50, truncated: false, known_vtuber_names: [],
      clusters: [{
        suggested_original: 'ミカ', existing_term: null, total: 5,
        spellings: [
          { text: 'ミカ', count: 3, sources: ['speaker'], is_alias: false, is_term: false },
          { text: 'ミッカ', count: 2, sources: ['katakana'], is_alias: false, is_term: false },
        ],
        examples: [{ line_idx: 2, snippet: 'ミカが笑う' }],
      }],
    },
  }))
  const glossary = await openGlossary(page)
  await glossary.getByRole('button', { name: 'Find possible names' }).click()
  const names = glossary.getByRole('region', { name: 'Possible names' })
  await expect(names).toContainText('ミカ · ミッカ')
  await expect(names).toContainText('may be one name')
  await expect(names.getByRole('button', { name: 'Make a term from ミカ' })).toBeDisabled()
  await expect(names.getByText('Still needed: a series (above).')).toBeVisible()
})

test('empty results say so for both scans', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/variants?*', (route) => route.fulfill({ json: { ...VARIANTS, candidates: [] } }))
  await page.route('**/api/glossary/dramas/1/name-clusters', (route) => route.fulfill({
    json: { available: true, reason: '', lines_scanned: 50, truncated: false, clusters: [], known_vtuber_names: [] },
  }))
  const glossary = await openGlossary(page)
  await glossary.getByRole('button', { name: 'Find similar spellings of ヤロ' }).click()
  await expect(glossary.getByRole('region', { name: 'Similar spellings of ヤロ' })).toContainText('No other spellings found in 120 lines.')
  await glossary.getByRole('button', { name: 'Find possible names' }).click()
  await expect(glossary.getByRole('region', { name: 'Possible names' })).toContainText('No recurring names found yet.')
})
