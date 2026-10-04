import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'
import { installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the stronger-engine offer and its result
// fit the width with 44px targets (Step 99). The try is mocked.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const shotDir = process.env.STRONGER_SHOTS_DIR

function python(code: string): string {
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot }).toString()
}

const CAPABILITY = '/api/settings/engine-routing/capabilities/translation.high_quality'
let ids: number[] = []

test.beforeEach(async ({ request }) => {
  const out = python(`
import json
from core import Line
db.update_drama(3, audio_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='他终于离开了这座城市', en='He finally left the city', flag='uncertain'),
    Line(idx=1, start=2.0, end=3.5, zh='好的', en='Okay'),
])
print(json.dumps([r['id'] for r in db.load_lines(3)]))
`)
  ids = JSON.parse(out.trim().split('\n').pop() as string)
  expect((await request.post(CAPABILITY, { data: { engine: 'deepseek' } })).ok()).toBe(true)
})

test.afterAll(async ({ request }) => {
  await request.post(CAPABILITY, { data: { engine: null } })
  python('db.save_lines(3, [])')
})

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTouchTargets(page: Page, selector: string, min = 1) {
  const sizes = await page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => [window.hitHeight(e), e.outerHTML.slice(0, 80)] as const),
  )
  expect(sizes.length).toBeGreaterThanOrEqual(min)
  for (const [h, html] of sizes) expect(h, html).toBeGreaterThanOrEqual(44)
}

test('stronger-engine offer and result fit a phone', async ({ page }) => {
  await page.route('**/api/stronger-engine/dramas/3/lines/*/try', (route) =>
    route.fulfill({ json: {
      drama_id: 3, line_id: ids[0], engine: 'deepseek', model: 'deepseek-chat',
      text: 'At long last he walked away from the city he had called home for so many years',
      based_on_en: 'He finally left the city', cost_usd: 0.0004,
    } }))
  await page.goto('/#/drama/3/review')
  const row = page.locator(`.review-line[data-line-id="${ids[0]}"]`)
  await expect(row).toHaveAttribute('aria-current', 'true')
  const offer = row.getByTestId('line-stronger')
  await expect(offer.getByTestId('line-stronger-reasons')).toHaveText('Flagged in review')
  await expectTouchTargets(page, '[data-testid="line-stronger"] button', 1)
  await expectNoHorizontalOverflow(page)
  await row.scrollIntoViewIfNeeded()
  if (shotDir) await page.screenshot({ path: `${shotDir}/stronger-phone-offer.png` })

  await offer.getByRole('button', { name: /^Try with DeepSeek/ }).tap()
  const res = offer.getByTestId('line-stronger-result')
  await expect(res.getByTestId('line-stronger-cost')).toHaveText('Cost: under $0.001')
  await expectTouchTargets(page, '[data-testid="line-stronger-result"] button', 2)
  await expectNoHorizontalOverflow(page)
  await res.scrollIntoViewIfNeeded()
  if (shotDir) await page.screenshot({ path: `${shotDir}/stronger-phone.png` })

  await res.getByRole('button', { name: 'Use this' }).tap()
  await expect(row.getByTestId('line-en')).toHaveText('At long last he walked away from the city he had called home for so many years')
})
