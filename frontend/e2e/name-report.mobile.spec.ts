import { expect, test } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844): the name-check panel fits the width and its buttons stay reachable.
test('the name check fits a phone', async ({ page }) => {
  const long = 'Ａ very long invented title name that would push a row wider than a phone if it could not wrap'
  const group = {
    key: 'yalo', canonical: 'Yalo', total: 6, titles_total: 2, mark: null,
    variants: [{ text: 'Yalo', count: 4 }, { text: 'Yaloch', count: 2 }, { text: 'Yaloooooooooooooooooooooch', count: 1 }],
    titles: [
      { drama_id: 1, title: long, count: 4, in_glossary: true, glossary_term: 'Yalo', glossary_english: 'Yarrow' },
      { drama_id: 2, title: 'Beta Stream', count: 2, in_glossary: false, glossary_term: null, glossary_english: null },
    ],
    examples: [{ drama_id: 2, line_idx: 4, snippet: 'then Yaloch left through the door without a word' }],
  }
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/name-report', (route) => route.fulfill({ json: {
    job_id: 'glossary_name_report_pc', status: 'done', progress: 1, message: '',
    result: { titles_total: 2, titles_scanned: 2, titles_unavailable: 0, titles_failed: 0, reasons: [], truncated: false, stopped_early: false,
      singletons_omitted: 0, groups_omitted: 0, groups: [group], counts: { unresolved: 1, right: 0, wrong: 0, skip: 0 } },
  } }))
  await page.goto('/#/drama/1/translate')
  await page.locator('details.section', { hasText: 'Glossary' }).first().locator(':scope > summary').click()
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  await glossary.getByRole('button', { name: 'Check names across all titles' }).click()
  const panel = glossary.getByRole('region', { name: 'Name check across all titles' })
  await expect(panel.getByRole('listitem', { name: 'Name Yalo' })).toBeVisible()

  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const name of ['Mark Yalo right', 'Mark Yalo wrong', 'Mark Yalo skip', 'Check again', 'Copy report']) {
    expect(await hitHeight(panel.getByRole('button', { name, exact: true }).first()), `${name} touch target`)
      .toBeGreaterThanOrEqual(44)
  }
})
