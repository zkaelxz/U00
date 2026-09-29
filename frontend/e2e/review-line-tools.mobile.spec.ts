import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults } from './reviewResultsSeed'

// Phone project (390x844, touch): the per-line tools (sheet entries, the
// alternatives and grammar panels, the TM bar) and the flagged-line buttons
// fit the width with 44px targets, and "Next flagged" crosses to page 2.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const shotDir = path.join(repoRoot, 'frontend', 'test-results', 'review-line-tools')

function python(code: string): string {
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot }).toString()
}

let ids: number[] = []

test.beforeEach(() => {
  const out = python(`
import json
from core import Line
db.update_drama(3, audio_filename=None)
lines = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'句子{i}', en=f'Line {i}') for i in range(45)]
lines[0].flag = 'uncertain'
lines[43].flag = 'uncertain'
db.save_lines(3, lines)
print(json.dumps([r['id'] for r in db.load_lines(3)]))
`)
  ids = JSON.parse(out.trim().split('\n').pop() as string)
})

test.afterAll(() => {
  clearReviewResults()
  python('db.save_lines(3, [])')
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const row = (page: Page, idx: number) => page.locator(`.review-line[data-line-id="${ids[idx]}"]`)

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTouchTargets(page: Page, selector: string, min = 1) {
  const sizes = await page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => [e.getBoundingClientRect().height, e.outerHTML.slice(0, 80)] as const),
  )
  expect(sizes.length).toBeGreaterThanOrEqual(min)
  for (const [h, html] of sizes) expect(h, html).toBeGreaterThanOrEqual(44)
}

test('per-line tools fit a phone; next flagged crosses pages', async ({ page }) => {
  const long = 'A rather long alternative rendering that has to wrap on a narrow phone screen without any sideways scrolling at all'
  await page.route('**/api/line-ai/dramas/3/lines/*/alternatives', (route) =>
    route.fulfill({ json: {
      line_id: ids[0], current_en: 'Line 0', engine: 'x', model: null,
      alternatives: [{ translation: long, approach: 'natural', tradeoff: 'length' }],
    } }))
  await page.route('**/api/line-ai/dramas/3/lines/*/grammar', (route) =>
    route.fulfill({ json: {
      line_id: ids[0], zh: '句子0', engine: 'x', model: null,
      parts: [{ word: '句子', reading: 'jùzi', meaning: 'a sentence, a phrase, a clause in running text', function: 'noun (subject)' }],
    } }))
  await page.route('**/api/review/dramas/3/tm-suggestions*', (route) =>
    route.fulfill({ json: [{ line_id: ids[0], line_idx: 0, zh: '句子0', en: 'Line 0', suggestion: long, similarity: 0.9, exact: false, entry_id: 1 }] }))

  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(40)
  await expect(row(page, 0)).toHaveAttribute('aria-current', 'true')
  await expect(row(page, 0).getByTestId('line-tm')).toBeVisible()
  await expectTouchTargets(page, '.review-flagnav button, .review-tm button', 4)
  await expectNoHorizontalOverflow(page)

  await row(page, 0).getByRole('button', { name: 'More actions for line 1' }).tap()
  for (const item of ['Alternatives (AI)', 'Grammar breakdown (AI)', 'Pronounce the source']) {
    await expect(page.getByRole('button', { name: item })).toBeVisible()
  }
  await page.getByRole('button', { name: 'Alternatives (AI)' }).tap()
  await expect(row(page, 0).getByTestId('line-alternatives')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectTouchTargets(page, '[data-testid="line-tools-panel"] button', 2)
  await page.screenshot({ path: `${shotDir}/phone-alternatives.png`, fullPage: false })

  await row(page, 0).getByRole('button', { name: 'More actions for line 1' }).tap()
  await page.getByRole('button', { name: 'Grammar breakdown (AI)' }).tap()
  await expect(row(page, 0).getByTestId('line-grammar')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.screenshot({ path: `${shotDir}/phone-grammar.png`, fullPage: false })

  await page.getByRole('button', { name: 'Next flagged ›' }).tap()
  await expect(row(page, 43)).toHaveAttribute('aria-current', 'true')
  await expect(rows(page)).toHaveCount(5)
})
