import { expect, test, type Page } from '@playwright/test'

// Parity T03/T04/X13 on the Translate stage's Glossary panel. The seeded
// dramas have no series, so the terms list, the import and the bulk delete
// are mocked; catalogues and instructions hit the real seeded API.

const TERMS = [
  { id: 11, term_original: '师姐', term_translation: 'Senior Sister', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] },
  { id: 12, term_original: '沈清疑', term_translation: 'Shen Qingyi', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] },
]

async function openGlossary(page: Page) {
  await page.goto('/#/drama/1/translate')
  await page.locator('details.section', { hasText: 'Glossary' }).first().locator(':scope > summary').click()
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  return glossary
}

test('imports pasted text, asks before replacing, and offers the CSV', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: TERMS }))
  const bodies: unknown[] = []
  await page.route('**/api/glossary/dramas/1/import', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({
      json: { added: ['a'], overwritten: bodies.length > 1 ? ['师姐'] : [], skipped_existing: bodies.length > 1 ? [] : ['师姐'], invalid: [], warnings: ['No header row detected.'] },
    })
  })
  const glossary = await openGlossary(page)
  await glossary.getByText('Import or export').click()
  await expect(glossary.getByRole('link', { name: 'Download glossary as CSV' })).toHaveAttribute('href', /\/api\/glossary\/dramas\/1\/export\.csv$/)

  await glossary.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(glossary.getByRole('alert').filter({ hasText: 'Paste a glossary' })).toBeVisible()
  expect(bodies).toHaveLength(0)

  await glossary.getByLabel('Or paste the glossary').fill('a,A\n师姐,Sis')
  await glossary.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(glossary.getByTestId('glossary-import-result')).toContainText('Added 1 term, kept 1 existing.')
  await expect(glossary.getByTestId('glossary-import-result')).toContainText('No header row detected.')
  expect(bodies[0]).toEqual({ text: 'a,A\n师姐,Sis' })

  await glossary.getByLabel('Replace terms that are already in the glossary').check()
  await glossary.getByRole('button', { name: 'Import', exact: true }).click()
  expect(bodies).toHaveLength(1)
  await glossary.getByRole('button', { name: 'Yes, import and replace' }).click()
  await expect(glossary.getByTestId('glossary-import-result')).toContainText('replaced 1')
  expect(bodies[1]).toEqual({ text: 'a,A\n师姐,Sis', overwrite_existing: true, confirm: true })
})

test('a loaded file sends its text and name', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: [] }))
  const bodies: unknown[] = []
  await page.route('**/api/glossary/dramas/1/import', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { added: ['x'], overwritten: [], skipped_existing: [], invalid: [], warnings: [] } })
  })
  const glossary = await openGlossary(page)
  await glossary.getByText('Import or export').click()
  await expect(glossary.getByRole('link', { name: 'Download glossary as CSV' })).toHaveCount(0)
  await glossary.getByLabel('Glossary file').setInputFiles({ name: 'terms.tsv', mimeType: 'text/tab-separated-values', buffer: Buffer.from('x\tX\n') })
  await expect(glossary.getByLabel('Or paste the glossary')).toHaveValue('x\tX\n')
  await glossary.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(glossary.getByTestId('glossary-import-result')).toContainText('Added 1 term.')
  expect(bodies[0]).toEqual({ text: 'x\tX\n', filename: 'terms.tsv' })
})

test('away from the PC only pasting is offered and nothing is replaced', async ({ page }) => {
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }),
  )
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: TERMS }))
  const bodies: unknown[] = []
  await page.route('**/api/glossary/dramas/1/import', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { added: ['a'], overwritten: [], skipped_existing: ['师姐'], invalid: [], warnings: [] } })
  })
  const glossary = await openGlossary(page)
  await glossary.getByText('Import or export').click()
  await expect(glossary.getByText('Choosing a file and replacing existing terms are PC only')).toBeVisible()
  await expect(glossary.getByLabel('Glossary file')).toHaveCount(0)
  await expect(glossary.getByLabel('Replace terms that are already in the glossary')).toHaveCount(0)
  await glossary.getByLabel('Paste the glossary').fill('a,A\n师姐,Sis')
  await glossary.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(glossary.getByTestId('glossary-import-result')).toContainText('Added 1 term, kept 1 existing.')
  expect(bodies).toEqual([{ text: 'a,A\n师姐,Sis' }])
})

test('bulk delete sends the chosen ids in one request', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: TERMS }))
  const bodies: unknown[] = []
  await page.route('**/api/glossary/dramas/1/terms/bulk-delete', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { deleted: [11], not_found: [12] } })
  })
  const glossary = await openGlossary(page)
  await glossary.getByLabel('Select all terms').check()
  await glossary.getByRole('button', { name: 'Delete selected (2)' }).click()
  await glossary.getByRole('button', { name: 'Yes, delete' }).click()
  await expect(glossary.getByText('1 of 2 term(s) were no longer in the glossary.')).toBeVisible()
  expect(bodies).toEqual([{ term_ids: [11, 12], confirm: true }])
})
