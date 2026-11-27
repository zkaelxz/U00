import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'
import { gotoTranslate } from './translateTabs'
import { expectPickedTab } from './tabPicker'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844): the suggest-spellings panel fits the width and its buttons stay
// reachable. The routes are mocked.

test('suggest spellings and the English check fit a phone', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({
    json: [{ id: 11, term_original: 'ナロ', term_translation: 'Nalo', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }],
  }))
  let ran = false
  await page.route('**/api/glossary/dramas/1/spelling-suggestions', (route) => {
    if (route.request().method() === 'POST') {
      ran = true
      return route.fulfill({ json: { job_id: 'spelling_suggest_1', engine: 'claude' } })
    }
    return route.fulfill({ json: ran
      ? { job_id: 'spelling_suggest_1', status: 'done', progress: 1, message: '', run_id: 'r1', result: {
        term: 'ナロ', english: 'Nalo', unusable: false, lines_scanned: 10, truncated: false,
        proposals: [{ text: 'ナロック', count: 3, is_alias: false, is_term: false, examples: [{ line_idx: 4, snippet: 'ナロックはどこだ、ここにいるのか' }] }] } }
      : { job_id: '', status: 'idle', progress: 0, message: '', run_id: null, result: null } })
  })
  await page.route('**/api/glossary/dramas/1/name-consistency?*', (route) => route.fulfill({
    json: {
      term: 'ナロ', english: 'Nalo', available: true, reason: '', lines_with_name: 3, untranslated: 0, consistent: 2,
      differing: [{ line_idx: 2, source: 'ナロが来たよ、今すぐここに来て', english: 'Naloch came, come here right now' }],
      other_words: ['Naloch'], lines_scanned: 3, truncated: false, more_rows: false,
    },
  }))
  await gotoTranslate(page, 'glossary')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expectPickedTab(glossary, 'Glossary', 'Terms')
  // The row's three buttons once squeezed the original to one letter per line (seen with smaller phone text).
  await page.addStyleTag({ content: 'html { font-size: 75%; }' })
  const original = glossary.getByRole('cell', { name: 'ナロ', exact: true })
  expect((await original.boundingBox())!.width).toBeGreaterThan(80)
  await glossary.getByRole('button', { name: 'Suggest spellings of ナロ' }).click()
  const panel = glossary.getByRole('region', { name: 'Suggest spellings of ナロ' })
  await panel.getByRole('button', { name: 'Suggest spellings', exact: true }).click()
  await panel.getByRole('button', { name: 'Add ナロック as alias' }).click()
  await expect(panel.getByRole('button', { name: 'Yes, add alias' })).toBeVisible()
  await panel.getByRole('button', { name: 'Check English names' }).click()
  await expect(panel.getByRole('region', { name: 'English name check' })).toContainText('Naloch came')

  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const name of ['Yes, add alias', 'Cancel', 'Check English names', 'Close']) {
    expect(await hitHeight(panel.getByRole('button', { name, exact: true }).first()), `${name} touch target`)
      .toBeGreaterThanOrEqual(44)
  }
})
