import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844): the similar-spellings panel and the possible-names list
// fit the width and their buttons stay reachable. Scans are mocked.

test('similar spellings and possible names fit a phone', async ({ page }) => {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({
    json: [{ id: 11, term_original: 'ヤロ', term_translation: 'Yalo', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }],
  }))
  await page.route('**/api/glossary/dramas/1/variants?*', (route) => route.fulfill({
    json: {
      term: 'ヤロ', available: true, reason: '', lines_scanned: 10, truncated: false,
      candidates: [{ text: 'ヤロック', count: 3, score: 0.83, is_alias: false, is_term: false, examples: [{ line_idx: 4, snippet: 'ヤロックはどこだ、ここにいるのか' }] }],
    },
  }))
  await page.route('**/api/glossary/dramas/1/name-clusters', (route) => route.fulfill({
    json: {
      available: true, reason: '', lines_scanned: 10, truncated: false, known_vtuber_names: [],
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
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByRole('tab', { name: 'Terms', exact: true })).toBeVisible()
  await glossary.getByRole('button', { name: 'Find similar spellings of ヤロ' }).click()
  const panel = glossary.getByRole('region', { name: 'Similar spellings of ヤロ' })
  await panel.getByRole('button', { name: 'Add ヤロック as alias' }).click()
  await expect(panel.getByRole('button', { name: 'Yes, add alias' })).toBeVisible()
  await glossary.getByRole('button', { name: 'Find possible names' }).click()
  await expect(glossary.getByRole('region', { name: 'Possible names' })).toContainText('ミカ · ミッカ')

  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const name of ['Yes, add alias', 'Cancel', 'Look again']) {
    expect(await hitHeight(glossary.getByRole('button', { name, exact: true }).first()), `${name} touch target`)
      .toBeGreaterThanOrEqual(44)
  }
})
